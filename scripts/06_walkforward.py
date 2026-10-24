# -*- coding: utf-8 -*-
"""
第 6 步：walk-forward 防过拟合检验（多因子策略参数稳健性）。

对每个测试年 Y（2022~2026，2026 为部分年份）：
  1. 用 Y 之前全部数据做参数选择：在网格上计算训练段信息比率（IR），取最优；
  2. 用选中参数在 Y 段做样本外（OOS）评估。
输出：
  - 每年 fold 的 选择参数 / 训练 IR / 样本外 IR；
  - OOS 链式年度净值 vs 同区间基准；
  - 参数稳定性统计；
  - "全样本最优参数"（作弊口径）与 walk-forward 的差距 —— 量化过拟合的真实代价。

用法: python scripts/06_walkforward.py [000300] [corrected|dividend]
  dividend = 红利版（低换手+低波+股息率，D-027），产物文件带 _div 后缀
"""
import sys

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401

from config import STORE_DIR
from src.data.storage import ParquetStore
from src.data import financials, universe
from src.data.market import get_kline
from src.analysis.factors import build_factor_panel
from src.strategies.multi_factor import (topk_dropout_topn,
                                         turnover_lowvol_dividend_topn,
                                         turnover_lowvol_reversal_topn)
from src.backtest.portfolio import run_portfolio_backtest

START_DATE = "20180101"
GRID = [(tn, rb) for tn in (20, 30, 50) for rb in (10, 20, 60)]
TEST_YEARS = [2022, 2023, 2024, 2025, 2026]


def metrics_of(res: dict, bench_ret: pd.Series) -> dict:
    nav = res["nav"]
    ret = res["daily_ret"]
    n = len(ret)
    if n == 0:
        return {"ann": np.nan, "ir": np.nan, "mdd": np.nan, "total": np.nan}
    ann = float(nav.iloc[-1] ** (250 / n) - 1)
    exc = ret - bench_ret.reindex(ret.index).fillna(0.0)
    ir = float(exc.mean() / exc.std() * np.sqrt(250)) if exc.std() > 0 else np.nan
    mdd = float((nav / nav.cummax() - 1).min())
    return {"ann": ann, "ir": ir, "mdd": mdd, "total": float(nav.iloc[-1] - 1)}


def run_combo(panel: pd.DataFrame, prices: pd.DataFrame, combo,
              dates: pd.DatetimeIndex, strategy: str = "corrected"):
    top_n, reb = combo
    if strategy == "dividend":
        W = turnover_lowvol_dividend_topn(panel, top_n=top_n, rebalance_days=reb)
    elif strategy == "topkdrop":
        W = topk_dropout_topn(panel,
                              [("turnover_20", -1), ("vol_20", -1), ("rev_5", -1)],
                              topk=top_n, n_drop=5, rebalance_days=reb)
    else:
        W = turnover_lowvol_reversal_topn(panel, top_n=top_n, rebalance_days=reb)
    p = prices.loc[dates]
    W = W[W.index.isin(p.index)]
    if W.empty:
        return None
    return run_portfolio_backtest(
        W, p, fee=0.00025, min_fee=5.0, slippage=0.0005,
        stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
        init_capital=1_000_000.0)


def main() -> None:
    index_code = sys.argv[1] if len(sys.argv) > 1 else "000300"
    strategy = sys.argv[2] if len(sys.argv) > 2 else "corrected"
    store = ParquetStore()
    pit = universe.load_pit_constituents(index_code, store)
    symbols = universe.pit_union_symbols(index_code, store)
    div_panel = None
    if strategy == "dividend":
        div_panel = financials.load_dividend_panel(store, symbols)
    print(f">> 构建 PIT 因子面板（{index_code}，{len(symbols)} 只，strategy={strategy}）...")
    panel = build_factor_panel(store, symbols, start=START_DATE,
                               pit_constituents=pit, dividend_panel=div_panel)
    prices = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{panel['symbol'].nunique()} 只，{len(panel)} 行")

    bench = get_kline(index_code, adjust="none", store=store)
    bench_ret = bench.set_index("date")["close"].pct_change().rename("bench")

    # 价格用 qfq（收益已含分红），不再叠加现金分红；见 DECISIONS.md

    all_dates = prices.index
    folds = []
    oos_rets = []
    oos_bench = []

    for y in TEST_YEARS:
        y0 = pd.Timestamp(f"{y}-01-01")
        y1 = pd.Timestamp(f"{y + 1}-01-01")
        train_dates = all_dates[all_dates < y0]
        test_dates = all_dates[(all_dates >= y0) & (all_dates < y1)]
        if len(test_dates) < 30:
            continue

        # ---- 训练段参数选择 ----
        best, best_ir = None, -np.inf
        for combo in GRID:
            res = run_combo(panel, prices, combo, train_dates, strategy)
            if res is None:
                continue
            m = metrics_of(res, bench_ret)
            if np.isfinite(m["ir"]) and m["ir"] > best_ir:
                best_ir, best = m["ir"], combo
        if best is None:
            continue

        # ---- 样本外评估 ----
        res_oos = run_combo(panel, prices, best, test_dates, strategy)
        if res_oos is None:
            continue
        m_oos = metrics_of(res_oos, bench_ret)
        oos_rets.append(res_oos["daily_ret"])
        oos_bench.append(bench_ret.reindex(test_dates).fillna(0.0))
        folds.append({"test_year": y, "combo": best,
                      "train_ir": round(best_ir, 3),
                      "oos_ir": round(m_oos["ir"], 3),
                      "oos_ann": round(m_oos["ann"] * 100, 2),
                      "oos_mdd": round(m_oos["mdd"] * 100, 2)})

    # ---- 汇总 ----
    oos = pd.concat(oos_rets).sort_index()
    ob = pd.concat(oos_bench).sort_index()
    nav_oos = (1.0 + oos).cumprod()
    nav_bench = (1.0 + ob).cumprod()
    total = float(nav_oos.iloc[-1] - 1)
    btotal = float(nav_bench.iloc[-1] - 1)
    ann = float(nav_oos.iloc[-1] ** (250 / len(oos)) - 1)
    exc = oos - ob
    ir = float(exc.mean() / exc.std() * np.sqrt(250)) if exc.std() > 0 else np.nan
    mdd = float((nav_oos / nav_oos.cummax() - 1).min())
    ann_exc = float(exc.mean() * 250)

    print()
    print("== walk-forward 逐年（训练选参 -> 样本外评估）==")
    fold_df = pd.DataFrame(folds)
    print(fold_df.to_string(index=False))

    print()
    print("== 样本外（OOS）汇总 ==")
    print(f"  区间: {oos.index[0].date()} ~ {oos.index[-1].date()}, {len(oos)} 交易日")
    print(f"  组合总收益: {total:.2%} | 年化 {ann:.2%}")
    print(f"  基准总收益: {btotal:.2%} | 超额 {total - btotal:.2%} | 年化超额 {ann_exc:.2%}")
    print(f"  信息比率: {ir:.2f} | 最大回撤: {mdd:.2%}")

    cnt: dict = {}
    for f in folds:
        cnt[f["combo"]] = cnt.get(f["combo"], 0) + 1
    print()
    print("== 参数稳定性（各 fold 选中的参数）==")
    for combo, c in sorted(cnt.items(), key=lambda kv: -kv[1]):
        print(f"  Top{combo[0]} / {combo[1]}日调仓: 被选中 {c} 次")

    # ---- 全样本最优（作弊口径）对比 ----
    best_all, best_all_ir = None, -np.inf
    for combo in GRID:
        res = run_combo(panel, prices, combo, all_dates, strategy)
        if res is None:
            continue
        m = metrics_of(res, bench_ret)
        if np.isfinite(m["ir"]) and m["ir"] > best_all_ir:
            best_all_ir, best_all = m["ir"], combo
    if best_all is not None:
        mask = all_dates >= pd.Timestamp(f"{TEST_YEARS[0]}-01-01")
        oos2_dates = all_dates[mask]
        res2 = run_combo(panel, prices, best_all, oos2_dates, strategy)
        m2 = metrics_of(res2, bench_ret) if res2 is not None else {}
        print()
        print("== 全样本最优 vs walk-forward（作弊对照）==")
        print(f"  全样本最优参数: Top{best_all[0]} / {best_all[1]}日调仓 "
              f"(全样本 IR={best_all_ir:.2f})")
        print(f"  该参数在样本外区间的表现: IR={m2.get('ir', float('nan')):.2f}, "
              f"年化={m2.get('ann', float('nan')):.2%}")
        print(f"  walk-forward 样本外: IR={ir:.2f}, 年化={ann:.2%}")
        print("  → 两者差距 = 参数过拟合的真实代价")

    try:
        from src.analysis.experiments import record
        record("walkforward",
               params={"index": index_code, "strategy": strategy,
                       "test_years": TEST_YEARS, "grid": str(GRID)},
               metrics={"oos_total": total, "oos_ann": ann, "oos_ir": ir,
                        "oos_mdd": mdd, "n_folds": len(folds)},
               notes="06 walk-forward 样本外（每年仅用此前数据选参）",
               artifacts=[f"walkforward_results/folds_{index_code}{suffix if (suffix := '_div' if strategy == 'dividend' else '') else ''}.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")

    out = STORE_DIR / "walkforward_results"
    out.mkdir(parents=True, exist_ok=True)
    suffix = {"dividend": "_div", "topkdrop": "_tk"}.get(strategy, "")
    fold_df.to_parquet(out / f"folds_{index_code}{suffix}.parquet", index=False)
    nav_oos.rename("nav").to_frame().to_parquet(out / f"nav_oos_{index_code}{suffix}.parquet")
    print()
    print(f">> 结果已保存到 {out}")


if __name__ == "__main__":
    main()
