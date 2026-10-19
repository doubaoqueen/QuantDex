# -*- coding: utf-8 -*-
"""组合级风控回归测试：单票上限、总仓位、回撤断路器。"""
import numpy as np
import pandas as pd

from src.risk.risk import apply_risk, cap_weights, drawdown_multiplier


def test_cap_single_weight():
    w = pd.Series([0.5, 0.3, 0.2], index=["A", "B", "C"])
    capped = cap_weights(w, max_single=0.10, max_total=1.0)
    assert (capped <= 0.10 + 1e-12).all()
    assert np.isclose(capped.sum(), 0.30, rtol=1e-12)


def test_cap_total_weight():
    w = pd.Series([0.5, 0.5], index=["A", "B"])
    capped = cap_weights(w, max_single=0.5, max_total=0.6)
    assert np.isclose(capped.sum(), 0.6, rtol=1e-12)
    assert np.allclose(capped.values, [0.3, 0.3], rtol=1e-12)


def test_drawdown_multiplier():
    nav = pd.Series([1.0, 1.2, 0.9, 1.1], index=pd.date_range("2024-01-01", periods=4))
    m = drawdown_multiplier(nav, max_dd=-0.15, scale=0.5)
    assert m.iloc[0] == 1.0      # 无回撤
    assert m.iloc[1] == 1.0      # 新高
    assert m.iloc[2] == 0.5      # 回撤 0.9/1.2-1=-25% < -15%
    assert m.iloc[3] == 1.0      # 1.1 相对峰值 1.2 仅回撤 8.3%，未达阈值


def test_apply_risk_scales_on_drawdown():
    dates = pd.date_range("2024-01-01", periods=3)
    W = pd.DataFrame({"A": [0.8, 0.8, 0.8], "B": [0.2, 0.2, 0.2]}, index=dates)
    nav = pd.Series([1.0, 1.0, 0.7], index=dates)   # 第 3 天回撤 -30%
    out = apply_risk(W, nav=nav, max_single=0.5, max_total=1.0,
                     max_dd=-0.15, dd_scale=0.5)
    assert np.isclose(out.loc[dates[0], "A"], 0.5)  # 超限截断
    assert np.isclose(out.loc[dates[1], "A"], 0.5)
    assert np.isclose(out.loc[dates[2], "A"], 0.25)  # 截断后再乘 0.5
    assert np.isclose(out.loc[dates[2], "B"], 0.1)
