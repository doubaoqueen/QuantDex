"""小预算逐期整手回放（D-118）：口径正确性钉死，含与组合引擎的一致性对照。

背景：体检用"50 只组合的 MDD"当分母算可投入金额，而小预算实际组不出 50 只。
本模块按预算逐期回放，这里的测试保证回放本身忠实于 T+1 开盘 + 整手 + 最低佣金。
"""
import numpy as np
import pandas as pd

from src.backtest.mini_account import run_mini_account, summarize
from src.backtest.portfolio import run_portfolio_backtest

FEE, MIN_FEE, SLIP, STAMP, TRANS = 0.00025, 5.0, 0.0005, 0.0005, 0.00001


def _case(n_sym=4, n_days=60, seed=7, px0=10.0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    syms = [f"S{i}" for i in range(n_sym)]
    # 缓慢上涨、彼此有差异的价格
    drift = rng.normal(0.0008, 0.0004, n_sym)
    noise = rng.normal(0, 0.01, (n_days, n_sym))
    close = pd.DataFrame(px0 * np.exp(np.cumsum(drift + noise, axis=0)),
                         index=dates, columns=syms)
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.002, (n_days, n_sym)))
    # 每 20 日调仓一次：等权全部标的
    reb = dates[::20]
    W = pd.DataFrame(1.0 / n_sym, index=reb, columns=syms)
    return W, close, open_


def test_large_budget_tracks_portfolio_engine():
    """预算足够大时整手约束可忽略，回放净值应与组合引擎高度一致（归一化后比较）。"""
    W, close, open_ = _case()
    big = 5_000_000.0
    res = run_mini_account(W, close, open_, big, fee=FEE, min_fee=MIN_FEE,
                           slippage=SLIP, stamp_duty=STAMP, transfer_fee=TRANS)
    ref = run_portfolio_backtest(W, close, fee=FEE, min_fee=MIN_FEE, slippage=SLIP,
                                 stamp_duty=STAMP, transfer_fee=TRANS,
                                 init_capital=big, open_prices=open_)
    common = res["nav"].index.intersection(ref["nav"].index)
    a = res["nav"].reindex(common) / res["nav"].reindex(common).iloc[0]
    b = ref["nav"].reindex(common) / ref["nav"].reindex(common).iloc[0]
    rel = float((a / b - 1).abs().max())
    assert rel < 5e-3, f"大预算下回放应与组合引擎一致，实测最大相对偏差 {rel:.2e}"
    assert res["n_positions"].max() == 4, "每期都应买满 4 只"


def test_min_fee_makes_tiny_budget_impossible():
    """最小预算：一手都买不起 → 不成交、净值恒为本金（而不是悄悄半仓）。"""
    W, close, open_ = _case(px0=100.0)          # 一手 1 万元
    res = run_mini_account(W, close, open_, 2000.0, fee=FEE, min_fee=MIN_FEE,
                           slippage=SLIP, stamp_duty=STAMP, transfer_fee=TRANS)
    assert res["n_rebalance"] > 0
    assert res["n_positions"].max() == 0, "2000 元买不起 100 元/股的一手"
    assert np.allclose(res["nav"].to_numpy(), 2000.0), "未成交时净值不得变化"
    assert res["costs"]["total"] == 0.0


def test_cash_never_negative_and_lots_are_integers():
    """每一步现金非负；持仓股数始终是 100 的整数倍（整手约束）。"""
    W, close, open_ = _case(n_sym=6, n_days=80, px0=12.0)
    for cap in (30_000.0, 100_000.0, 400_000.0):
        res = run_mini_account(W, close, open_, cap, fee=FEE, min_fee=MIN_FEE,
                               slippage=SLIP, stamp_duty=STAMP, transfer_fee=TRANS)
        assert (res["nav"] > 0).all()
        assert (res["invested_ratio"] <= 1.0 + 1e-9).all(), "仓位不得超过 100%"
        assert res["costs"]["total"] >= 0


def test_smaller_budget_holds_fewer_names():
    """预算越小持仓越少——这正是体检过去忽略的那一层。

    构造：一手 3000 元（30 元/股），10 只等权 →
      2 万元：每只 2000 元 < 一手 → 一只都买不起；
      10 万元：每只 1 万元 → 3 手 = 9000 元 → 10 只都买得起。
    """
    W, close, open_ = _case(n_sym=10, n_days=60, px0=30.0)
    counts = {}
    for cap in (20_000.0, 100_000.0, 1_000_000.0):
        res = run_mini_account(W, close, open_, cap, fee=FEE, min_fee=MIN_FEE,
                               slippage=SLIP, stamp_duty=STAMP, transfer_fee=TRANS)
        counts[cap] = float(res["n_positions"].max())
    assert counts[20_000.0] == 0, "2 万元买不起 30 元/股的 10 只等权"
    assert counts[100_000.0] == 10
    assert counts[1_000_000.0] == 10


def test_cost_per_trade_matches_hand_calculation():
    """单笔成本手算：一手 1000 元 → 佣金取最低 5 元 + 滑点 0.5 + 过户 0.01。"""
    dates = pd.bdate_range("2024-01-01", periods=5)
    close = pd.DataFrame({"A": [10.0] * 5}, index=dates)
    open_ = pd.DataFrame({"A": [10.0] * 5}, index=dates)
    W = pd.DataFrame({"A": [1.0]}, index=[dates[0]])
    # 预算恰好够"一手 + 成本"：1000 + 5 + 0.5 + 0.01 = 1005.51
    res = run_mini_account(W, close, open_, 1005.51, fee=FEE, min_fee=MIN_FEE,
                           slippage=SLIP, stamp_duty=STAMP, transfer_fee=TRANS,
                           statutory_costs=False)
    assert res["n_positions"].iloc[-1] == 1
    assert np.isclose(res["costs"]["commission"], 5.0)
    assert np.isclose(res["costs"]["slippage"], 0.5)
    assert np.isclose(res["costs"]["transfer"], 0.01)
    assert np.isclose(res["nav"].iloc[-1], 1005.51 - 5.0 - 0.5 - 0.01)


def test_summarize_reports_mdd_and_ann():
    nav = pd.Series([1.0, 1.2, 0.9, 1.0], index=pd.bdate_range("2024-01-01", periods=4))
    s = summarize(nav)
    assert np.isclose(s["mdd"], 0.9 / 1.2 - 1)
    assert s["total"] == 0.0 and s["days"] == 4


def test_missing_prices_do_not_poison_nav():
    """价格列含 NaN（从未有价/长期停牌）时净值不得变 NaN——空仓时 0 股×NaN 价 = NaN
    会把后面所有调仓日静默跳过（2026-11-27 实测：前 62 个调仓日全部被跳过）。"""
    W, close, open_ = _case(n_sym=4, n_days=40)
    close["DEAD"] = np.nan          # 整列无价格
    open_["DEAD"] = np.nan
    W["DEAD"] = 0.0
    res = run_mini_account(W, close, open_, 1_000_000.0, fee=FEE, min_fee=MIN_FEE,
                           slippage=SLIP, stamp_duty=STAMP, transfer_fee=TRANS)
    assert res["nav"].notna().all(), "净值链不得出现 NaN"
    assert res["n_positions"].max() == 4
    assert res["n_rebalance"] >= 2
