"""表达式引擎 + TopkDropout 选股回归测试。"""
import numpy as np
import pandas as pd

from src.analysis.expr import evaluate
from src.strategies.multi_factor import topk_dropout_topn


def test_expr_ref_mean_arithmetic():
    idx = pd.RangeIndex(30)
    close = pd.Series(np.linspace(10.0, 13.0, 30), index=idx)
    fields = {"close": close}
    r = evaluate("Ref($close, 1)", fields)
    assert abs(r.iloc[5] - close.iloc[4]) < 1e-12 and pd.isna(r.iloc[0])
    m = evaluate("Mean($close, 5)", fields)
    assert abs(m.iloc[9] - close.iloc[5:10].mean()) < 1e-12
    roc = evaluate("$close / Ref($close, 5) - 1", fields)
    assert abs(roc.iloc[10] - (close.iloc[10] / close.iloc[5] - 1)) < 1e-12
    # 负偏移 = 未来（只许标签用）
    fut = evaluate("Ref($close, -1)", fields)
    assert abs(fut.iloc[0] - close.iloc[1]) < 1e-12


def test_expr_div_by_zero_is_nan():
    a = pd.Series([1.0, -1.0, 2.0])
    out = evaluate("$close / $vol", {"close": a, "vol": pd.Series([0.0, 1.0, 2.0])})
    assert pd.isna(out.iloc[0]) and not np.isinf(out).any()


def test_topk_dropout_caps_churn():
    """TopkDropout 每期换出数 ≤ n_drop（换手上界机制）。"""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2020-01-01", periods=200, freq="B")
    syms = [f"S{i:03d}" for i in range(120)]
    rows = []
    for d in dates:
        for s in syms:
            rows.append({"date": d, "symbol": s, "f": rng.random()})
    panel = pd.DataFrame(rows)
    panel["f"] = panel.groupby("date")["f"].rank(pct=True)
    W = topk_dropout_topn(panel, [("f", -1)], topk=50, n_drop=5, rebalance_days=20)
    assert len(W) >= 2
    prev = None
    for d in W.index:
        cur = set(W.columns[W.loc[d] > 0])
        if prev is not None:
            # 换出数 = 上期持仓不在本期的数量，应 ≤ n_drop
            n_out = len(prev - cur)
            assert n_out <= 5, f"{d.date()} 换出 {n_out} 只，超过 n_drop=5"
        prev = cur
