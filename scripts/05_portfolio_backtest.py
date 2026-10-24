# -*- coding: utf-8 -*-
"""
第 5 步：组合 / 截面回测 —— 20日动量 + 低波 Top-N 等权，定期调仓，对比基准。

用法：
    python scripts/05_portfolio_backtest.py                 # 沪深300 PIT 池, Top50, 每20交易日调仓
    python scripts/05_portfolio_backtest.py 000905 30 20    # 指数 / TopN / 调仓间隔
前置：本地日线数据已由 04 脚本抓取（本脚本纯离线计算，不联网）。
输出：终端绩效 → data/store/portfolio_results/（净值 parquet + 回测图 PNG）
"""
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.storage import ParquetStore
from src.data import financials, universe
from src.data.market import get_kline
from src.analysis.factors import build_factor_panel
from src.analysis.plot import plot_backtest
from src.strategies.multi_factor import (momentum_lowvol_topn,
                                         turnover_lowvol_dividend_topn,
                                         turnover_lowvol_reversal_topn)
from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest

START_DATE = "20180101"


def main() -> None:
    index_code = sys.argv[1] if len(sys.argv) > 1 else "000300"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    rebalance = int(sys.argv[3]) if len(sys.argv) > 3 else 20

    store = ParquetStore()
    pit = universe.load_pit_constituents(index_code, store)
    symbols = universe.pit_union_symbols(index_code, store)

    print(f">> 构建 PIT 因子面板（{index_code}，{len(symbols)} 只历史成分）...")
    panel = build_factor_panel(store, symbols, start=START_DATE,
                               pit_constituents=pit)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{panel['symbol'].nunique()} 只，{len(panel)} 行")

    strategy = sys.argv[4] if len(sys.argv) > 4 else "corrected"
    div_panel = None
    if strategy == "dividend":
        try:
            div_panel = financials.load_dividend_panel(store, symbols)
        except Exception as e:  # noqa: BLE001
            print(f"   [warn] 分红面板加载失败: {e}")
            strategy = "corrected"
    if div_panel is not None:
        panel = build_factor_panel(store, symbols, start=START_DATE,
                                   pit_constituents=pit, dividend_panel=div_panel)
    print(f">> 策略[{strategy}]：每 {rebalance} 交易日调仓，Top {top_n} 等权")
    if strategy == "legacy":
        W = momentum_lowvol_topn(panel, top_n=top_n, rebalance_days=rebalance)
    elif strategy == "dividend":
        W = turnover_lowvol_dividend_topn(panel, top_n=top_n, rebalance_days=rebalance)
    else:
        W = turnover_lowvol_reversal_topn(panel, top_n=top_n, rebalance_days=rebalance)
    print(f"   信号日 {len(W)} 个，覆盖标的 {W.shape[1]} 只")

    prices = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    print(f"   价格矩阵 {prices.shape[0]} 交易日 × {prices.shape[1]} 只")

    bench_ret = None
    try:
        b = get_kline(index_code, adjust="none", store=store)
        b = b.set_index("date")["close"]
        bench_ret = b.pct_change().rename("bench")
        print("   基准：", index_code)
    except FileNotFoundError:
        print("   [warn] 无指数日线，跳过基准对比（可在 02 脚本补指数后再跑）")

    # 保真口径：最低佣金 5 元/笔、过户费 0.001%、涨跌停/停牌跳过、100 万名义本金
    # 注意：价格用前复权（qfq），其收益已包含分红效应（除权日价格平滑），
    #       因此这里不再叠加现金分红（否则双重计算；见 DECISIONS.md）
    res = run_portfolio_backtest(
        W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
        stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
        init_capital=1_000_000.0, benchmark_returns=bench_ret)
    print(f"   保真约束：跳过 {res['n_skipped']} 笔（涨停买不进/跌停卖不出/停牌）")
    c = res["costs"]
    print(f"   成本合计 {c['total']:,.2f} 元（佣金 {c['commission']:,.0f} "
          f"印花税 {c['stamp_duty']:,.0f} 过户费 {c['transfer']:,.0f} "
          f"滑点 {c['slippage']:,.0f}）")
    perf = portfolio_performance(res)

    print()
    print("== 组合绩效 ==")
    for k, v in perf.items():
        if k == "调仓次数":
            print(f"  {k}: {int(v)}")
        elif k == "年均换手率":
            print(f"  {k}: {v:.0%}")
        elif isinstance(v, float) and k in ("夏普比率", "Calmar比率", "信息比率"):
            print(f"  {k}: {v:.2f}")
        elif isinstance(v, float) and k.startswith("成本"):
            print(f"  {k}: {v:,.0f} 元")
        elif isinstance(v, float):
            print(f"  {k}: {v:.2%}")
        else:
            print(f"  {k}: {v}")

    out = STORE_DIR / "portfolio_results"
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{index_code}_top{top_n}_reb{rebalance}" + ("_div" if strategy == "dividend" else "")
    res["nav"].rename("nav").to_frame().to_parquet(out / f"nav_{tag}.parquet")
    W.to_parquet(out / f"weights_{tag}.parquet")

    # 复用 plot.plot_backtest：构造其所需列
    plot_df = pd.DataFrame({"date": res["dates"], "nav": res["nav"].values,
                            "drawdown": res["drawdown"].values})
    if "benchmark_nav" in res:
        plot_df["benchmark_nav"] = res["benchmark_nav"].values
    try:
        p = plot_backtest(plot_df, title=f"{index_code} 动量+低波 Top{top_n} 等权",
                          name=tag, out_dir=out / "plots")
        print()
        print(f">> 回测图已保存: {p}")
    except Exception as e:  # noqa: BLE001
        print()
        print(f"[warn] 出图失败: {e}")


if __name__ == "__main__":
    main()
