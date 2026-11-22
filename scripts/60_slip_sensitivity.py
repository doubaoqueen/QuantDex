"""第 60 步：滑点敏感性全表（归因强制口径②完整版，D-098；周末清单 2026-11-23 收口）。

corrected Top50/20 canonical（05 号保真口径：佣金含最低 5 元/印花/过户/涨跌停停牌
跳过/T+1 开盘执行）在滑点 5/15/20/30bp 下的收益衰减全表——回应"alpha 是否靠少算
滑点撑起来"。此前 11-21/22 的表为 ad-hoc 产物（无脚本源）；本脚本使其可复现，
复算结果与 ad-hoc 版逐位对照（5bp 锚 = D-108 canonical）。

用法：python scripts/60_slip_sensitivity.py
落盘：data/store/research_results/slippage_sensitivity.csv（覆盖）+ experiments 存档。
"""
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.factors import build_factor_panel
from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest
from src.data import universe
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import turnover_lowvol_reversal_topn

START = "20180101"
RATES_BP = [5, 15, 20, 30]
OUT = STORE_DIR / "research_results" / "slippage_sensitivity.csv"


def main() -> None:
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 构建面板（{len(symbols)} 只）...", flush=True)
    panel = build_factor_panel(store, symbols, start=START,
                               pit_constituents=pit, exclude_st=True)
    W = turnover_lowvol_reversal_topn(panel, top_n=50, rebalance_days=20)
    prices, opens = load_price_matrix(symbols, start=START, store=store)
    panel_dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel["date"]).unique()))
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"价格矩阵 {prices.shape}", flush=True)

    rows = []
    for bp in RATES_BP:
        res = run_portfolio_backtest(
            W, prices, fee=0.00025, min_fee=5.0, slippage=bp / 10000.0,
            stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
            init_capital=1_000_000.0, open_prices=opens)
        perf = portfolio_performance(res)
        rows.append({"slippage_bp": bp,
                     "ann": perf["年化收益率"],
                     "sharpe": perf["夏普比率"],
                     "mdd": perf["最大回撤"],
                     "total": perf["总收益率"]})
        print(f"   {bp:2d}bp: 年化 {perf['年化收益率']:+.2%} | 总 {perf['总收益率']:+.2%} "
              f"| MDD {perf['最大回撤']:.2%} | Sharpe {perf['夏普比率']:.3f}", flush=True)

    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"\n>> 落盘 {OUT}")
    try:
        record("slip_sensitivity_full",
               params={"rates_bp": RATES_BP, "strategy": "corrected",
                       "top_n": 50, "rebalance": 20},
               metrics={f"{bp}bp_total": r["total"] for r, bp in zip(rows, RATES_BP, strict=False)},
               notes="归因强制口径②完整版（滑点 5/15/20/30bp）；canonical 05 保真口径",
               artifacts=["research_results/slippage_sensitivity.csv"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
