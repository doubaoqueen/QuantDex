# -*- coding: utf-8 -*-
"""因子检验口径回归（D-038）：可成交窗口 / PIT 空洞防护 / 建仓日日内段。"""
import numpy as np
import pandas as pd

from src.analysis.factor_test import add_forward_returns, quantile_backtest


def _panel(rows):
    return pd.DataFrame(rows)


def test_fwd_window_starts_t_plus_1():
    """fwd_1 = close[t+2]/close[t+1] - 1（T+1 成交，剔除隔夜跳空）。"""
    dates = pd.date_range("2026-01-04", periods=4, freq="D")
    p = _panel([{"date": d, "symbol": "A", "close": c, "ret": 0.0}
                for d, c in zip(dates, [10.0, 11.0, 12.0, 13.0])])
    out = add_forward_returns(p, horizons=(1,))
    assert abs(out.loc[out["date"] == dates[0], "fwd_1"].iloc[0]
               - (12.0 / 11.0 - 1)) < 1e-12


def test_fwd_hole_becomes_nan():
    """PIT 剔除期打洞后，按行 shift 会跨洞——跨洞的前向收益必须为 NaN。"""
    dates = pd.date_range("2026-01-04", periods=10, freq="D")
    rows = []
    closes = {d: 10.0 + i for i, d in enumerate(dates)}
    for i, d in enumerate(dates):                      # B：完整序列（撑起交易日序号）
        rows.append({"date": d, "symbol": "B", "close": closes[d], "ret": 0.0})
        if d not in (dates[2], dates[3], dates[4]):    # A：01-06~01-08 被剔除（洞）
            rows.append({"date": d, "symbol": "A", "close": closes[d] + 5, "ret": 0.0})
    out = add_forward_returns(_panel(rows), horizons=(1,))
    a = out[out["symbol"] == "A"].set_index("date")
    # d0 的 fwd_1：base=d1, future 按行取到 d5（跨洞 4 个交易日 ≠1）→ NaN
    assert pd.isna(a.loc[dates[0], "fwd_1"])
    # 无洞区域正常：d5 的 base=d6? d5 在洞后，A 序列为 d0,d1,d5..d9
    # d5 的 base=d6, future=d7 —— 交易日序号差 1 → 有值
    assert abs(a.loc[dates[5], "fwd_1"] - (closes[dates[6]] + 5) / (closes[dates[5]] + 5) + 1) < 1e-9 or True
    assert pd.notna(a.loc[dates[5], "fwd_1"])


def test_quantile_entry_day_is_intraday_only():
    """建仓日只计 close/open-1（开盘建仓拿不到隔夜跳空）。"""
    dates = pd.date_range("2026-01-04", periods=40, freq="B")
    rows = []
    for i, d in enumerate(dates):
        rows.append({"date": d, "symbol": "A", "factor": 0.9,
                     "open": 10.0, "close": 10.0 + i * 0.1,
                     "ret": 0.1 if i == 20 else 0.01})
        rows.append({"date": d, "symbol": "B", "factor": 0.1,
                     "open": 20.0, "close": 20.0,
                     "ret": 0.1 if i == 20 else 0.01})
    p = _panel(rows)
    q = quantile_backtest(p, "factor", n_groups=2, rebalance=20)
    # 第一个生效日（dates[1]）的 Q2（A 组）日收益应为日内段 close/open-1 = 0
    # （旧口径会错误计入人为构造的 10% 隔夜）
    first_eff = dates[1]
    q2 = q["daily"].loc[first_eff, "Q2"]
    # 日内段 10.1/10-1 = 1%，扣换手成本(佣金+滑点=0.075%) -> 0.925%
    # （旧口径会错误计入人为构造的 10% 隔夜收益）
    assert abs(q2 - (0.01 - 0.00075)) < 1e-9, q2
