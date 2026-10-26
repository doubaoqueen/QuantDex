"""开盘执行口径回归（D-034）：T+1 开盘成交的两段收益与独立手算一致。"""
import pandas as pd

from src.backtest.portfolio import run_portfolio_backtest


def test_open_execution_matches_hand_calc():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    close = pd.DataFrame({"A": [10.0, 10.0, 11.0]}, index=dates)
    open_ = pd.DataFrame({"A": [10.0, 10.5, 11.0]}, index=dates)
    W = pd.DataFrame({pd.Timestamp(dates[0]): {"A": 1.0}}).T
    W.index.name = "date"

    res = run_portfolio_backtest(W, close, fee=0, min_fee=0, slippage=0,
                                 stamp_duty=0, transfer_fee=0,
                                 trade_lag=1, open_prices=open_)
    # 信号日 d0（收盘）-> d1 开盘全仓买入：
    #   隔夜 = 10.5/10 - 1 = 5%（空仓吃不到 -> 0）
    #   日内 = 11/10.5 - 1 = 4.7619%
    # d2 收盘卖出前后均持有：11->11 = 0%
    hand = (11.0 / 10.5)
    assert abs(res["nav"].iloc[-1] - hand) < 1e-12, res["nav"].iloc[-1]


def test_open_execution_limit_up_blocks_buy():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    # 主板 10%：d1 开盘 11.05 = 昨收 10 涨 10.5% -> 涨停买不进，保持空仓
    close = pd.DataFrame({"A": [10.0, 11.1, 11.1]}, index=dates)
    open_ = pd.DataFrame({"A": [10.0, 11.05, 11.1]}, index=dates)
    W = pd.DataFrame({pd.Timestamp(dates[0]): {"A": 1.0}}).T
    res = run_portfolio_backtest(W, close, fee=0, min_fee=0, slippage=0,
                                 stamp_duty=0, transfer_fee=0,
                                 trade_lag=1, limit_skip=True, open_prices=open_)
    assert res["n_skipped"] >= 1
    assert abs(res["nav"].iloc[-1] - 1.0) < 1e-12  # 全程空仓
