"""表达式引擎 + TopkDropout 选股回归测试。"""
import numpy as np
import pandas as pd

from src.analysis.expr import evaluate, evaluate_wide
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


# ---------------- 宽面板模式与 Alpha101 新算子（2026-11-14） ----------------
def _wide_frame(base, seed=3):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    idx = pd.date_range('2026-01-01', periods=30)
    return pd.DataFrame(rng.normal(base, 1.0, (30, 4)),
                        index=idx, columns=['s1', 's2', 's3', 's4'])


def test_rank_cross_sectional_wide():
    """Rank：横截面百分位（宽面板 axis=1），严格递增行末列 = 1.0。"""
    f = {'close': _wide_frame(10.0)}
    f['close'].iloc[0] = [1.0, 2.0, 3.0, 4.0]          # 递增行
    r = evaluate_wide('Rank($close)', f)
    assert abs(r.iloc[0]['s4'] - 1.0) < 1e-9 and abs(r.iloc[0]['s1'] - 0.25) < 1e-9


def test_tsrank_head_nan_and_tail():
    """TsRank：窗口不足为 NaN；严格递增列末值 = 1.0。"""
    f = {'close': _wide_frame(10.0)}
    f['close'].iloc[:, 0] = np.linspace(9.0, 12.0, 30)  # 严格递增
    r = evaluate_wide('TsRank($close, 5)', f)
    assert np.isnan(r.iloc[2]['s1'])
    assert r.iloc[-1]['s1'] == 1.0


def test_decay_linear_weights():
    """DecayLinear：权重 d..1 归一化（3 日 = 3/6, 2/6, 1/6）。"""
    f = {'close': _wide_frame(10.0)}
    c = f['close']
    f['close'].iloc[-3:] = [[10.0, 10.0, 10.0, 10.0],
                            [20.0, 20.0, 20.0, 20.0],
                            [30.0, 30.0, 30.0, 30.0]]
    r = evaluate_wide('DecayLinear($close, 3)', f)
    assert abs(r.iloc[-1]['s1'] - (30 * 3 + 20 * 2 + 10) / 6) < 1e-9


def test_if_and_comparisons():
    """If 条件分支 + 比较运算（Alpha101 三元式 x ? y : z 的翻译基础）。"""
    f = {'close': _wide_frame(10.0)}
    r = evaluate_wide('If($close > 10, 1, 0)', f)
    assert set(r.stack().unique()) <= {0.0, 1.0}
    assert (r.where(f['close'] > 10).dropna().stack().unique() == [1.0]).all()


def test_scale_cross_sectional():
    """Scale：逐日 |x| 归一到 sum=1（宽面板 axis=0 除法）。"""
    f = {'close': _wide_frame(10.0)}
    f['close'] = f['close'].abs()
    r = evaluate_wide('Scale($close)', f)
    assert np.allclose(r.abs().sum(axis=1), 1.0, atol=1e-9)
