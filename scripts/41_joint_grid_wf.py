"""第 41 步：联合网格 walk-forward——{因子集}×{参数} 一起选（D-074 resolution，D-075）。

D-074 的证据分裂：固定 Top50/20日 下 2 因子胜（+63.2% vs +40.1%），联合选参的
WF 里 3 因子反超（IR 0.39 vs 0.34）。根因：rev_5 的拖累是"5 日因子 × 20 日调仓"
的**交互产物**——因子清单与参数不能分开评估。

本脚本把两者放进同一个网格联合选择：
  因子集 {corrected 3因子, corrected_2f 2因子} × TopN {20,30,50} × 调仓 {20,60} 日
  = 12 个组合，每个组合一次全程回测（策略对 (组合,面板) 确定、无路径依赖，
  全程净值按年切片即可得任意训练/测试窗的 IR——比逐折重建快百倍）。
逐折规则（与 06 同语义）：测试年 2019-2026，训练=此前全部年份，按训练期超额 IR
选最优组合，该组合的测试年表现记为 OOS。聚合 OOS = 联合选择下的真实样本外。

产出：{2 因子, 3 因子} 各自"联合选择"后的 OOS 对比 → rev_5 去留的最终答案。
口径：面板为 ST 剔除口径（与 D-071 canonical 同源），保真成本，开盘执行。
用法：python scripts/41_joint_grid_wf.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factors import build_factor_panel
from src.analysis.experiments import record
from src.backtest.portfolio import run_portfolio_backtest
from src.data import universe
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import composite_topn

OUT_DIR = STORE_DIR / "research_results"
FACTOR_SETS = {
    "3因子": [("turnover_20", -1), ("vol_20", -1), ("rev_5", -1)],
    "2因子": [("turnover_20", -1), ("vol_20", -1)],
}
TOP_NS = (20, 30, 50)
REBS = (20, 60)
TEST_YEARS = range(2019, 2027)


def ir_of(nav: pd.Series, ret: pd.Series, bench: pd.Series,
          start: pd.Timestamp, end: pd.Timestamp) -> float:
    """区间超额 IR（对沪深300 日超额的年化）。"""
    m = (nav.index >= start) & (nav.index <= end)
    if m.sum() < 60:
        return np.nan
    r = ret[m] - bench.reindex(ret[m].index).fillna(0.0)
    te = float(r.std() * np.sqrt(250))
    return float(r.mean() * 250 / te) if te > 0 else np.nan


def main() -> None:
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 构建面板（{len(symbols)} 只，exclude_st=True）...", flush=True)
    panel = build_factor_panel(store, symbols, start="20180101",
                               pit_constituents=pit, exclude_st=True)
    panel_dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel["date"]).unique()))
    prices, opens = load_price_matrix(symbols, start="20180101", store=store)
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    b = get_kline("000300", adjust="none", store=store).set_index("date")["close"]
    bench_ret = b.pct_change()

    # 12 个组合各一次全程回测
    combos = []
    for fs_name, factors in FACTOR_SETS.items():
        for top_n in TOP_NS:
            for reb in REBS:
                combos.append((fs_name, top_n, reb))
    results = {}
    for i, (fs_name, top_n, reb) in enumerate(combos, 1):
        W = composite_topn(panel, FACTOR_SETS[fs_name], top_n=top_n, rebalance_days=reb)
        res = run_portfolio_backtest(
            W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
            stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
            init_capital=1_000_000.0, benchmark_returns=bench_ret,
            open_prices=opens)
        results[(fs_name, top_n, reb)] = res
        print(f"   [{i}/{len(combos)}] {fs_name} Top{top_n}/{reb}日 "
              f"总收益 {res['nav'].iloc[-1] - 1:+.1%}", flush=True)

    # 逐折联合选择：训练期超额 IR 最优的组合 → 该年 OOS
    fold_rows = []
    oos_by_fs = {"3因子": [], "2因子": []}
    for year in TEST_YEARS:
        start = pd.Timestamp(f"{year}-01-01")
        end = pd.Timestamp(f"{year}-12-31")
        train_end = start - pd.Timedelta(days=1)
        best, best_ir = None, -np.inf
        for combo in combos:
            r = results[combo]
            ir = ir_of(r["nav"], r["daily_ret"], bench_ret,
                       pd.Timestamp("2018-01-01"), train_end)
            if pd.notna(ir) and ir > best_ir:
                best_ir, best = ir, combo
        r = results[best]
        oos_ir = ir_of(r["nav"], r["daily_ret"], bench_ret, start, end)
        m = (r["nav"].index >= start) & (r["nav"].index <= end)
        n_y = r["nav"][m]
        oos_ret = float(n_y.iloc[-1] / n_y.iloc[0] - 1) if len(n_y) > 1 else np.nan
        oos_by_fs[best[0]].append(oos_ret)
        fold_rows.append({"year": year, "chosen": f"{best[0]}-Top{best[1]}/{best[2]}",
                          "train_ir": best_ir, "oos_ret": oos_ret, "oos_ir": oos_ir})
        print(f"   {year}: 训练选 [{best[0]}-Top{best[1]}/{best[2]}日] "
              f"(train IR {best_ir:+.2f}) → OOS {oos_ret:+.1%}", flush=True)

    folds = pd.DataFrame(fold_rows)
    print("\n== 联合选择下的 OOS 汇总（2019-2026）==")
    for fs, rets in oos_by_fs.items():
        if not rets:
            continue
        comp = float(np.prod([1 + x for x in rets]) - 1)
        print(f"  {fs}: 逐年 OOS 复利总收益 {comp:+.1%}（{len(rets)} 折）")
    print("\n逐年明细：")
    print(folds.to_string(index=False,
                          float_format=lambda v: f"{v:+.3f}" if isinstance(v, float) else str(v)))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    folds.to_parquet(OUT_DIR / "joint_grid_wf_folds.parquet", index=False)
    try:
        record("joint_grid_wf",
               params={"grid": "2因子×3参数×2调仓 = 12 组合", "test_years": "2019-2026"},
               metrics={f"{fs}|oos_comp": float(np.prod([1 + x for x in rets]) - 1)
                        for fs, rets in oos_by_fs.items() if rets},
               notes="联合选择 WF：rev_5 去留的最终答案，见 DECISIONS D-075",
               artifacts=["research_results/joint_grid_wf_folds.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
