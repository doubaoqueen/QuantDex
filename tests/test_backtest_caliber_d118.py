"""回测口径修正回归（D-118，2026-11-26）：法定费率时间线、全收益基准近似、按日 ST 判定。

三条都是"回测说谎"的同一类问题——用今天的规则去算历史：
  ① 印花税/过户费有过下调，旧回测全程按现行值计，2018~2023-08 的卖出少算一半印花税；
  ② 基准用价格指数，"超额"里近一半只是指数自己的股息；
  ③ 回测侧拿不到名称，主板历史 ST 一律按 ±10% 判涨跌停（F-21）。
"""
import numpy as np
import pandas as pd

from config import (
    STAMP_RATE,
    STAMP_RATE_HISTORY,
    TRANSFER_RATE,
    TRANSFER_RATE_HISTORY,
    rate_on,
)
from src.backtest.portfolio import benchmark_total_return, run_portfolio_backtest

FEE = 0.00025
SLIP = 0.0005


def test_rate_on_boundaries():
    """费率在生效日当天切换；None 取现行值；早于全部生效日取首条。"""
    assert rate_on(STAMP_RATE_HISTORY, "2023-08-25") == 0.0010
    assert rate_on(STAMP_RATE_HISTORY, "2023-08-28") == 0.0005
    assert rate_on(STAMP_RATE_HISTORY, "2026-01-05") == STAMP_RATE
    assert rate_on(STAMP_RATE_HISTORY) == STAMP_RATE
    assert rate_on(STAMP_RATE_HISTORY, pd.Timestamp("2018-01-02")) == 0.0010
    assert rate_on(TRANSFER_RATE_HISTORY, "2022-04-28") == 0.00002
    assert rate_on(TRANSFER_RATE_HISTORY, "2022-04-29") == TRANSFER_RATE


def _sell_only_case(exec_date: str):
    """首日建仓 A 满仓，执行日清仓——清仓当天的印花税/过户费是唯一的口径差异来源。"""
    d0 = pd.Timestamp(exec_date) - pd.Timedelta(days=7)
    dates = pd.date_range(d0, periods=9, freq="D")
    dates = dates[dates <= pd.Timestamp(exec_date)]
    assert dates[-1] == pd.Timestamp(exec_date)
    px = pd.DataFrame({"A": [10.0] * len(dates)}, index=dates)
    W = pd.DataFrame({"A": [1.0, 0.0]}, index=[dates[0], dates[-2]])
    return px, W


def test_statutory_costs_charge_double_stamp_before_cut():
    px, W = _sell_only_case("2023-08-25")          # 印花税减半前一个交易日清仓
    old = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP_RATE,
                                 transfer_fee=TRANSFER_RATE)
    new = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP_RATE,
                                 transfer_fee=TRANSFER_RATE, statutory_costs=True)
    # 这一天的法定印花税是 0.1%（传入值 0.05% 的一半口径），翻倍
    assert np.isclose(new["costs"]["stamp_duty"], 2 * old["costs"]["stamp_duty"])
    # 过户费在 2022-04-29 已减半 → 当天两口径相同
    assert np.isclose(new["costs"]["transfer"], old["costs"]["transfer"])
    assert new["statutory_costs"] is True and old["statutory_costs"] is False
    assert new["nav"].iloc[-1] < old["nav"].iloc[-1]


def test_statutory_costs_charge_double_transfer_before_cut():
    px, W = _sell_only_case("2022-04-28")          # 过户费减半前一个交易日清仓
    old = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP_RATE,
                                 transfer_fee=TRANSFER_RATE)
    new = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP_RATE,
                                 transfer_fee=TRANSFER_RATE, statutory_costs=True)
    # 过户费 0.002%（传入值 0.001% 的翻倍）；印花税当时已是 0.1% → 也翻倍
    assert np.isclose(new["costs"]["transfer"], 2 * old["costs"]["transfer"])
    assert np.isclose(new["costs"]["stamp_duty"], 2 * old["costs"]["stamp_duty"])


def test_statutory_costs_equal_flat_after_cut():
    px, W = _sell_only_case("2023-08-28")          # 减半生效日之后：两口径必须一致
    old = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP_RATE,
                                 transfer_fee=TRANSFER_RATE)
    new = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP_RATE,
                                 transfer_fee=TRANSFER_RATE, statutory_costs=True)
    assert np.allclose(old["nav"].to_numpy(), new["nav"].to_numpy(), rtol=0, atol=1e-12)
    assert new["costs"] == old["costs"]


def test_benchmark_total_return_accrues_dividend():
    # 251 个价格点 → 250 个日收益；价格不动的 250 天，全收益累计恰好 = 股息率
    close = pd.Series(np.full(251, 100.0), index=pd.date_range("2024-01-01", periods=251))
    tr = benchmark_total_return(close)
    assert np.isclose(float(np.prod(1 + tr.iloc[1:])) - 1, 0.02, atol=1e-9)
    assert np.isnan(tr.iloc[0])
    assert (tr.iloc[1:] > 0).all()
    moving = pd.Series([100.0, 101.0], index=pd.date_range("2024-01-01", periods=2))
    one = benchmark_total_return(moving, div_yield=0.0)
    assert np.isclose(one.iloc[1], 0.01)


def _limit_case(st_flag):
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    # 执行日开盘 +6%：对主板非 ST（±10%）可买，对主板 ST（±5%）已涨停买不进
    px = pd.DataFrame({"A": [10.0, 10.6, 11.0]}, index=dates)
    op = pd.DataFrame({"A": [10.0, 10.6, 11.0]}, index=dates)
    W = pd.DataFrame({"A": [1.0]}, index=[dates[0]])
    res = run_portfolio_backtest(W, px, fee=0.0, slippage=0.0, limit_skip=True,
                                 open_prices=op, st_flag=st_flag)
    return float(res["nav"].iloc[-1]), res["n_skipped"]


def test_st_flag_tightens_limit_for_st_days():
    flag = pd.DataFrame({"symbol": ["A"] * 3, "date": pd.date_range("2024-01-01", periods=3),
                         "is_st": [0, 1, 1]})
    nav_st, skipped_st = _limit_case(flag)
    nav_plain, skipped_plain = _limit_case(None)
    # 当日是 ST → +6% 即涨停 → 买不进，空仓
    assert skipped_st == 1 and nav_st == 1.0
    # 不传表 / 当日非 ST → 可以买入，吃到次日 +3.77%
    assert skipped_plain == 0 and np.isclose(nav_plain, 11.0 / 10.6)
    flag_non_st = flag.assign(is_st=[0, 0, 0])
    assert _limit_case(flag_non_st)[1] == 0
    # 表里没有这只股票的记录 → 按非 ST 处理（不报错）
    other = pd.DataFrame({"symbol": ["Z"], "date": [pd.Timestamp("2024-01-02")], "is_st": [1]})
    assert _limit_case(other)[1] == 0
