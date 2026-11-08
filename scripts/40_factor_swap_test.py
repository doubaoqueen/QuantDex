"""第 40 步：因子换入实验——TSTD20 换入 + rev_5 去留验证（D-073 排期，D-074）。

D-073 闸门的两条指向，本实验一次测完（同面板同引擎，唯一变量=因子清单）：
  A 基线：corrected = turnover_20 + vol_20 + rev_5（现行，ST 已剔除口径）
  B 换入：TSTD20 替换 turnover_20（同族换强：闸门测得 IC -0.0675 vs -0.052）
  C 去尾：turnover_20 + vol_20（去掉 rev_5——分解实验 + 闸门双重指向其拖累）
  D 合并：TSTD20 + vol_20（换强 + 去拖累）
全部 Top50/20 日等权、保真成本、T+1 开盘执行、ST 剔除（与 D-071 新 canonical 同口径）。
判断：全期 + OOS(2022+) 双窗口；胜出者再做 walk-forward（06 需注册新策略，下一步）。

注意：这是研究实验，不动策略定义——胜出且过 WF 后才由用户拍板切换影子盘。

用法：python scripts/40_factor_swap_test.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factors import build_factor_panel
from src.backtest.portfolio import run_portfolio_backtest
from src.data import universe
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import composite_topn

OUT_DIR = STORE_DIR / "research_results"
OOS_START = "2022-01-01"

ARMS = {
    "A基线(现行)": [("turnover_20", -1), ("vol_20", -1), ("rev_5", -1)],
    "B换入TSTD20": [("TSTD20", -1), ("vol_20", -1), ("rev_5", -1)],
    "C去rev_5": [("turnover_20", -1), ("vol_20", -1)],
    "D换强+去尾": [("TSTD20", -1), ("vol_20", -1)],
}


def main() -> None:
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 构建面板（{len(symbols)} 只，exclude_st=True）...", flush=True)
    from src.analysis import expr_factors as EF
    panel = build_factor_panel(store, symbols, start="20180101",
                               pit_constituents=pit, exclude_st=True,
                               expr_factors={"TSTD20": EF.EXPR_FACTORS["TSTD20"]["expr"]})
    print(f"   面板 {panel['date'].min().date()}~{panel['date'].max().date()}，"
          f"{len(panel)} 行，池 {panel['symbol'].nunique()} 只", flush=True)

    prices, opens = load_price_matrix(symbols, start="20180101", store=store)
    panel_dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel["date"]).unique()))
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    b = get_kline("000300", adjust="none", store=store).set_index("date")["close"]
    bench_ret = b.pct_change()

    rows = []
    for tag, factors in ARMS.items():
        missing = [f for f, _ in factors if f not in panel.columns]
        if missing:
            print(f"   [error] {tag} 缺因子 {missing}")
            continue
        W = composite_topn(panel, factors, top_n=50, rebalance_days=20)
        res = run_portfolio_backtest(
            W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
            stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
            init_capital=1_000_000.0, benchmark_returns=bench_ret,
            open_prices=opens)
        nav, ret = res["nav"], res["daily_ret"]
        ann = nav.iloc[-1] ** (250 / max(len(nav), 1)) - 1
        mdd = float((nav / nav.cummax() - 1).min())
        row = {"arm": tag, "total": float(nav.iloc[-1] - 1), "ann": float(ann),
               "mdd": mdd, "calmar": ann / abs(mdd) if mdd < 0 else np.nan,
               "turnover": float(res["turnover"].sum() / max(len(nav), 1) * 250)}
        oos = nav.index >= pd.Timestamp(OOS_START)
        if oos.any():
            n2 = nav[oos]
            row["oos_total"] = float(n2.iloc[-1] / n2.iloc[0] - 1)
            oos_days = max(len(n2), 1)
            row["oos_ann"] = float((n2.iloc[-1] / n2.iloc[0]) ** (250 / oos_days) - 1)
            dd2 = float((n2 / n2.cummax() - 1).min())
            row["oos_mdd"] = dd2
            row["oos_calmar"] = row["oos_ann"] / abs(dd2) if dd2 < 0 else np.nan
        rows.append(row)
        print(f"   [done] {tag}", flush=True)

    res = pd.DataFrame(rows)
    print("\n== 因子换入实验（保真口径，Top50/20日，开盘执行，ST 已剔除）==")
    print(f"{'方案':<14} {'全期':>9} {'年化':>8} {'MDD':>8} {'Calmar':>7} {'换手':>6} "
          f"| {'OOS':>9} {'OOS年化':>8} {'OOS_MDD':>8} {'OOS_Calmar':>7}")
    for _, r in res.iterrows():
        print(f"{r['arm']:<14} {r['total']:>+9.1%} {r['ann']:>+8.1%} {r['mdd']:>8.1%} "
              f"{r['calmar']:>7.2f} {r['turnover']:>5.0%} "
              f"| {r['oos_total']:>+9.1%} {r['oos_ann']:>+8.1%} {r['oos_mdd']:>8.1%} "
              f"{r['oos_calmar']:>7.2f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT_DIR / "factor_swap_test.parquet", index=False)
    try:
        from src.analysis.experiments import record
        best = res.loc[res["oos_calmar"].idxmax()]
        record("factor_swap_test",
               params={"arms": list(ARMS), "panel": "ST 剔除口径"},
               metrics={f"{r['arm']}|oos_calmar": float(r["oos_calmar"])
                        for _, r in res.iterrows()},
               notes="TSTD20 换入 + rev_5 去留实验：结论见 DECISIONS D-074",
               artifacts=["research_results/factor_swap_test.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
