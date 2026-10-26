"""
D-011 市场状态过滤器回测：弱市约束"新开仓"能否改善 MDD/收益？

候选规则（信号日 T 收盘判定，T+1 收盘执行——与引擎口径一致，无前视）：
  R1  基准(沪深300)收盘 < MA200       -> 新开仓权重减半（腾出的仓位转为现金，不再分配）
  R2  基准收盘 < MA60 且 市场宽度 < 30% -> 暂停新开仓（新标的权重置 0）
  其中"新开仓" = 目标 Top-N 中、上一调仓期未持有的标的；已持仓标的不受影响。

对比四个组合（其余参数与 05 保真口径完全一致）：
  base 基线 / r1 / r2 / r1+r2
通过标准（D-011 原文）：改善 MDD/收益才启用。

输出：data/store/portfolio_results/regime_filter_comparison.parquet + 终端表
用法：python scripts/21_regime_filter_backtest.py [指数] [TopN] [调仓日]
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factors import build_factor_panel
from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest
from src.data import universe
from src.data.market import get_kline
from src.data.storage import ParquetStore
from src.strategies.multi_factor import turnover_lowvol_reversal_topn

START_DATE = "20180101"
BREADTH_MA = 60          # 宽度 = 站上 MA60 的成分股比例
BREADTH_TH = 0.30        # R2 宽度阈值（D-011：宽度 < 30%）


def apply_regime_filter(W: pd.DataFrame, bench_close: pd.Series,
                        breadth: pd.Series, use_r1: bool, use_r2: bool,
                        verbose: bool = False) -> tuple[pd.DataFrame, dict]:
    """按 R1/R2 缩减每个调仓日的『新开仓』权重；返回新权重矩阵与触发统计。"""
    ma60 = bench_close.rolling(60).mean()
    ma200 = bench_close.rolling(200).mean()
    out = W.copy()
    prev_set: set = set()
    stat = {"r1_days": 0, "r2_days": 0, "r1_cut": 0, "r2_cut": 0}
    for d in W.index:
        row = out.loc[d]
        tgt = set(row.index[row > 0])
        new = tgt - prev_set
        b = bench_close.reindex([d]).iloc[0]
        m60 = ma60.reindex([d]).iloc[0]
        m200 = ma200.reindex([d]).iloc[0]
        br = breadth.reindex([d]).iloc[0] if d in breadth.index else np.nan
        r1 = use_r1 and pd.notna(m200) and b < m200
        r2 = use_r2 and pd.notna(m60) and pd.notna(br) and b < m60 and br < BREADTH_TH
        if r1:
            stat["r1_days"] += 1
            for s in new:
                out.loc[d, s] = row[s] * 0.5
                stat["r1_cut"] += 1
        if r2:
            stat["r2_days"] += 1
            for s in new:
                out.loc[d, s] = 0.0
                stat["r2_cut"] += 1
        if verbose and (r1 or r2):
            print(f"   [{d.date()}] R1={r1} R2={r2} bench={b:.0f} "
                  f"MA60={m60:.0f} MA200={m200:.0f} breadth={br if pd.isna(br) else f'{br:.0%}'}")
        prev_set = tgt
    return out, stat


def main() -> None:
    index_code = sys.argv[1] if len(sys.argv) > 1 else "000300"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    rebalance = int(sys.argv[3]) if len(sys.argv) > 3 else 20

    store = ParquetStore()
    pit = universe.load_pit_constituents(index_code, store)
    symbols = universe.pit_union_symbols(index_code, store)
    print(f">> 构建 PIT 因子面板（{index_code}，{len(symbols)} 只历史成分）...", flush=True)
    panel = build_factor_panel(store, symbols, start=START_DATE, pit_constituents=pit)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{panel['symbol'].nunique()} 只，{len(panel)} 行", flush=True)

    W0 = turnover_lowvol_reversal_topn(panel, top_n=top_n, rebalance_days=rebalance)
    print(f">> 基线策略：低换手+低波+反转 Top{top_n}，每 {rebalance} 交易日调仓，"
          f"信号日 {len(W0)} 个", flush=True)

    prices = panel.pivot(index="date", columns="symbol", values="close").sort_index()

    # 市场宽度：每日站上 MA60 的成分股比例（面板内可得样本）
    ma = prices.rolling(BREADTH_MA).mean()
    above = (prices > ma).sum(axis=1)
    valid = prices.notna() & ma.notna()
    breadth = (above / valid.sum(axis=1)).dropna()
    print(f"   宽度序列 {breadth.index.min().date()} ~ {breadth.index.max().date()}"
          f"（最近一日 {breadth.iloc[-1]:.0%}）", flush=True)

    b = get_kline(index_code, adjust="none", store=store)
    bench = b.set_index("date")["close"].sort_index()
    bench.index = pd.to_datetime(bench.index)
    bench_ret = bench.pct_change()

    variants = {"base": (False, False), "r1": (True, False),
                "r2": (False, True), "r1+r2": (True, True)}
    rows, navs = [], {}
    for name, (u1, u2) in variants.items():
        W, stat = apply_regime_filter(W0, bench, breadth, u1, u2)
        res = run_portfolio_backtest(
            W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
            stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
            init_capital=1_000_000.0, benchmark_returns=bench_ret)
        perf = portfolio_performance(res)
        navs[name] = res["nav"]
        rows.append({
            "variant": name,
            "总收益率": perf["总收益率"], "年化收益率": perf["年化收益率"],
            "最大回撤": perf["最大回撤"], "Calmar比率": perf["Calmar比率"],
            "夏普比率": perf["夏普比率"], "年均换手率": perf["年均换手率"],
            "基准总收益率": perf.get("基准总收益率", np.nan),
            "r1触发日": stat["r1_days"], "r2触发日": stat["r2_days"],
            "r1减半笔数": stat["r1_cut"], "r2禁开笔数": stat["r2_cut"],
        })
        print(f">> [{name}] 总收益 {perf['总收益率']:+.2%} | 年化 {perf['年化收益率']:+.2%} | "
              f"MDD {perf['最大回撤']:.2%} | Calmar {perf['Calmar比率']:.2f} | "
              f"R1触发 {stat['r1_days']} 日/减半 {stat['r1_cut']} 笔，"
              f"R2触发 {stat['r2_days']} 日/禁开 {stat['r2_cut']} 笔", flush=True)

    out = pd.DataFrame(rows)
    out_dir = STORE_DIR / "portfolio_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_dir / "regime_filter_comparison.parquet", index=False)
    pd.DataFrame(navs).to_parquet(out_dir / "regime_filter_navs.parquet")

    print()
    print("== 市场状态过滤器对比（保真口径，2018-01 ~ 今）==")
    show = out.copy()
    for c in ["总收益率", "年化收益率", "最大回撤", "年均换手率", "基准总收益率"]:
        if c in show.columns:
            show[c] = show[c].map(lambda v: f"{v:+.2%}")
    for c in ["Calmar比率", "夏普比率"]:
        show[c] = show[c].map(lambda v: f"{v:.2f}")
    print(show.to_string(index=False))
    print(f"\n已保存: {out_dir}/regime_filter_comparison.parquet")
    print("判定标准（D-011）：MDD 改善且收益不被明显吃掉 -> 才考虑启用并接入影子盘。")


if __name__ == "__main__":
    main()
