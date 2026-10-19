# -*- coding: utf-8 -*-
"""单标的向量化引擎回归测试：T+1 成交、佣金、滑点、印花税。"""
import numpy as np
import pandas as pd

from src.backtest.engine import run_backtest

FEE = 0.00025
SLIP = 0.0005
STAMP = 0.0005


def _make_df():
    dates = pd.date_range("2024-01-01", periods=4, freq="D")
    df = pd.DataFrame({"date": dates, "open": [10.0, 11.0, 12.0, 13.0],
                       "close": [10.0, 11.0, 12.0, 13.0]})
    # 策略已把信号后移一日：第 0 天空仓，第 1-2 天持仓，第 3 天平仓
    df["position"] = [0, 1, 1, 0]
    return df


def test_t1_and_cost_hand_calc():
    df = _make_df()
    out, _ = run_backtest(df, price_col="open", fee=FEE, slippage=SLIP, stamp_duty=STAMP)

    # 独立手算（按约定公式重写一遍）：ret 为开盘价日收益；当日收益来自前一日仓位
    opens = df["open"].to_numpy()
    pos = df["position"].to_numpy()
    nav = 1.0
    for i in range(1, len(df)):
        r = opens[i] / opens[i - 1] - 1
        strat_ret = pos[i - 1] * r
        chg = pos[i] - pos[i - 1]
        buys = 1.0 if chg > 0 else 0.0
        sells = 1.0 if chg < 0 else 0.0
        cost = (buys + sells) * SLIP + buys * FEE + sells * (FEE + STAMP)
        nav *= 1.0 + strat_ret - cost
    assert np.isclose(out["nav"].iloc[-1], nav, rtol=1e-12), (out["nav"].iloc[-1], nav)

    # 关键口径断言
    assert out["position"].tolist() == [0, 1, 1, 0]
    assert np.isclose(out["cost"].iloc[1], SLIP + FEE, rtol=1e-12)    # 建仓日
    assert np.isclose(out["cost"].iloc[3], SLIP + FEE + STAMP, rtol=1e-12)  # 平仓日含印花税
    assert np.isclose(out["strat_ret"].iloc[0], 0.0)                  # 第 0 天无仓位不赚


def test_no_trade_no_return_when_flat():
    df = _make_df()
    df["position"] = 0
    out, trades = run_backtest(df, price_col="open", fee=FEE, slippage=SLIP, stamp_duty=STAMP)
    assert np.isclose(out["nav"].iloc[-1], 1.0, rtol=1e-12)
    assert len(trades) == 0
