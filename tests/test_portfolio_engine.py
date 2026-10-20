# -*- coding: utf-8 -*-
"""组合回测引擎回归测试：T+1 生效、权重漂移、换手成本、印花税、现金。"""
import numpy as np
import pandas as pd

from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest

FEE = 0.00025
SLIP = 0.0005
STAMP = 0.0005


def test_suspended_non_target_not_counted():
    """停牌判断只针对真正需要调仓的标的（回归：曾把所有无行情标的都计入跳过）。"""
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    prices = pd.DataFrame({
        "A": [10.0, 10.0, 10.0],
        "B": [np.nan, np.nan, np.nan],   # B 全程无行情、且目标权重为 0
    }, index=dates)
    W = pd.DataFrame({"A": [1.0], "B": [0.0]}, index=[dates[0]])
    res = run_portfolio_backtest(W, prices, fee=0.00025, slippage=0.0,
                                 limit_skip=True)
    assert res["n_skipped"] == 0


def test_min_fee_dominates_small_capital():
    """小资金：最低佣金 5 元/笔吃掉大头（1000 元调仓 → 0.5% 成本）。"""
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"A": [10.0, 10.0, 10.0]}, index=dates)
    W = pd.DataFrame({"A": [1.0]}, index=[dates[0]])
    res = run_portfolio_backtest(W, prices, fee=0.00025, slippage=0.0,
                                 min_fee=5.0, init_capital=1000.0)
    # 执行日买入 1000 元：佣金 max(0.25, 5)=5 元 → 净值 0.995
    assert abs(float(res["nav"].iloc[-1]) - 0.995) < 1e-9


def test_limit_up_blocks_buy():
    """执行日涨停 → 买不进，保持空仓。"""
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"A": [10.0, 11.0, 11.0]}, index=dates)  # +10% 涨停
    W = pd.DataFrame({"A": [1.0]}, index=[dates[0]])
    res = run_portfolio_backtest(W, prices, fee=0.00025, slippage=0.0,
                                 limit_skip=True)
    assert res["n_skipped"] == 1
    assert abs(float(res["nav"].iloc[-1]) - 1.0) < 1e-9


def test_suspended_blocks_trade():
    """执行日停牌（价格缺失）→ 跳过调仓。"""
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    prices = pd.DataFrame({"A": [10.0, np.nan, 10.0]}, index=dates)
    W = pd.DataFrame({"A": [1.0]}, index=[dates[0]])
    res = run_portfolio_backtest(W, prices, fee=0.00025, slippage=0.0,
                                 limit_skip=True)
    assert res["n_skipped"] == 1
    assert abs(float(res["nav"].iloc[-1]) - 1.0) < 1e-9


def _prices():
    idx = pd.date_range("2024-01-01", periods=6, freq="D")
    return idx, pd.DataFrame({
        "A": [10, 11, 12, 13, 14, 15],
        "B": [20, 20, 20, 20, 20, 20],
    }, index=idx)


def _hand_nav(opens_a, opens_b, w_a, w_b, rebalance_at, fee=FEE, slip=SLIP, stamp=STAMP):
    """独立实现：T+1 生效、调仓扣费、权重漂移。"""
    nav = 1.0
    out = [1.0]
    for i in range(1, len(opens_a)):
        ra = opens_a[i] / opens_a[i - 1] - 1
        rb = opens_b[i] / opens_b[i - 1] - 1
        port = w_a * ra + w_b * rb
        nav *= 1 + port
        if port > -1:
            w_a = w_a * (1 + ra) / (1 + port)
            w_b = w_b * (1 + rb) / (1 + port)
        if i in rebalance_at:
            na, nb = rebalance_at[i]
            buy = max(na - w_a, 0) + max(nb - w_b, 0)
            sell = max(w_a - na, 0) + max(w_b - nb, 0)
            nav *= 1 - (buy * (fee + slip) + sell * (fee + slip + stamp))
            w_a, w_b = na, nb
        out.append(nav)
    return out


def test_hold_with_drift():
    idx, px = _prices()
    W = pd.DataFrame({"A": [0.5], "B": [0.5]}, index=[idx[0]])
    res = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP)
    hand = _hand_nav(px["A"].to_numpy(), px["B"].to_numpy(), 0.0, 0.0, {1: (0.5, 0.5)})
    got = [float(res["nav"].loc[d]) for d in idx]
    assert np.allclose(got, hand, rtol=1e-12), (np.round(got, 8), np.round(hand, 8))


def test_rebalance_sell_includes_stamp():
    idx, px = _prices()
    # day0 建仓 A=0.5, day3 清仓
    W = pd.DataFrame({"A": [0.5, 0.0]}, index=[idx[0], idx[3]])
    res = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP)
    hand = _hand_nav(px["A"].to_numpy(), px["B"].to_numpy(), 0.0, 0.0,
                     {1: (0.5, 0.0), 4: (0.0, 0.0)})
    got = [float(res["nav"].loc[d]) for d in idx]
    assert np.allclose(got, hand, rtol=1e-12), (np.round(got, 8), np.round(hand, 8))
    assert res["n_rebalance"] == 2


def test_cash_remainder_and_turnover():
    idx, px = _prices()
    W = pd.DataFrame({"A": [0.5], "B": [0.0]}, index=[idx[0]])
    res = run_portfolio_backtest(W, px, fee=FEE, slippage=SLIP, stamp_duty=STAMP)
    # 只买 0.5 仓位：建仓日换手 = 0.5
    assert np.isclose(res["turnover"].iloc[1], 0.5, rtol=1e-12)
    # 一半现金不产生收益：A 全程仅占 0.5 仓
    perf = portfolio_performance(res)
    assert len(res["nav"]) == len(idx)
    assert "总收益率" in perf


def test_empty_inputs_raises():
    idx, px = _prices()
    W = pd.DataFrame({"A": [0.5]}, index=[idx[0]])
    try:
        run_portfolio_backtest(W.iloc[0:0], px)
        raised = False
    except Exception:
        raised = True
    assert raised
