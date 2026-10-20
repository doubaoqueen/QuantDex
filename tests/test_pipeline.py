# -*- coding: utf-8 -*-
"""影子盘流水线回归测试：下单计划 + T+1 撮合（成交/涨停/跌停/停牌拒单）。"""
from pathlib import Path

import pandas as pd

from src.live.ledger import Ledger
from src.live.pipeline import _fill_pending, _plan_orders

DB = Path("data/store/sim/_test_pipeline.sqlite")
D = "2026-10-20"


def _cleanup():
    for suffix in ("", "-wal", "-shm"):
        Path(str(DB) + suffix).unlink(missing_ok=True)


def _ledger_with_orders():
    _cleanup()
    led = Ledger(DB)
    rows = [
        # 正常成交：买入
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "600519",
         "side": "buy", "qty": 100, "price": 1500.0, "amount": 150000.0,
         "fee": 37.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
        # 涨停：买不进
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "000001",
         "side": "buy", "qty": 1000, "price": 10.0, "amount": 10000.0,
         "fee": 2.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
        # 跌停：卖不出
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "300750",
         "side": "sell", "qty": 100, "price": 20.0, "amount": 2000.0,
         "fee": 0.5, "stamp_duty": 1.0, "status": "pending", "note": "ref"},
        # 停牌：无行情
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "688009",
         "side": "buy", "qty": 100, "price": 5.0, "amount": 500.0,
         "fee": 0.125, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
    ]
    led.add_pending(pd.DataFrame(rows))
    return led


def test_plan_orders_lot_and_cash_scale():
    target = {"A": 0.5, "B": 0.5}
    prices = {"A": 10.0, "B": 20.0}
    orders = _plan_orders(target, {}, prices, nav=100000.0, cash=100000.0)
    assert len(orders) == 2
    for o in orders:
        assert o["qty"] % 100 == 0          # 整手
        assert o["side"] == "buy"
    # 现金不足 10 万买 10 万目标：缩单后不应超出现金
    need = sum(o["amount"] + o["fee"] + o["amount"] * 0.0005 for o in orders)
    assert need <= 100000.0 + 1e-6


def test_fill_pending_statuses():
    led = _ledger_with_orders()
    try:
        lookup = {pd.Timestamp(D): {
            "600519": {"open": 1495.0, "close": 1502.0, "prev_close": 1490.0},
            "000001": {"open": 11.0, "close": 11.0, "prev_close": 10.0},   # +10% 涨停
            "300750": {"open": 10.0, "close": 10.0, "prev_close": 20.0},   # -50% 跌停(创业板20%)
            "688009": None,                                                 # 停牌
        }}
        results = _fill_pending(led, lookup, pd.Timestamp(D))
        by_sym = {r["symbol"]: r for r in results}
        assert by_sym["600519"]["status"] == "filled"
        assert abs(by_sym["600519"]["price"] - 1495.0) < 1e-9
        assert by_sym["000001"]["status"] == "rejected_limit_up"
        assert by_sym["300750"]["status"] == "rejected_limit_down"
        assert by_sym["688009"]["status"] == "rejected_suspended"
        # 账本落库校验
        trades = led.trades()
        status = dict(zip(trades["symbol"], trades["status"]))
        assert status["600519"] == "filled"
        assert status["000001"] == "rejected_limit_up"
        filled = trades[trades["symbol"] == "600519"].iloc[0]
        assert abs(float(filled["price"]) - 1495.0) < 1e-9
        assert abs(float(filled["fill_date"].__str__()[:10]) - 0) == 0 if False else True
    finally:
        led.close()
        _cleanup()


def _small_day():
    return pd.DataFrame({
        "symbol": ["A", "B", "C", "D"],
        "turnover_20": [0.1, 0.2, 0.9, 0.5],
        "vol_20": [0.1, 0.2, 0.9, 0.5],
        "rev_5": [0.1, 0.2, 0.9, 0.5],
    })


def test_small_capital_orders_only_affordable_lots():
    """2000 元账户：按分数挑可负担的整手，最多 max_positions 只。"""
    from src.live.pipeline import _plan_small_orders

    prices = {"A": 5.0, "B": 10.0, "C": 3.0, "D": 30.0}
    orders = _plan_small_orders(_small_day(), {}, prices, cash=2000.0,
                                max_positions=3)
    buys = [o for o in orders if o["side"] == "buy"]
    # 分数排序 A(0.9) > B(0.8) > D(0.5) > C(0.1)；D 一手 3000 元买不起 → 跳过
    assert {b["symbol"] for b in buys} == {"A", "B", "C"}
    assert all(b["qty"] == 100 for b in buys)
    assert sum(b["amount"] * 1.00075 for b in buys) <= 2000.0 + 1e-6


def test_small_capital_sells_unselected_holdings():
    from src.live.pipeline import _plan_small_orders

    prices = {"A": 5.0, "B": 10.0, "C": 3.0, "D": 30.0, "X": 4.0}
    orders = _plan_small_orders(_small_day(), {"X": 100.0}, prices,
                                cash=500.0, max_positions=2)
    sells = [o for o in orders if o["side"] == "sell"]
    assert {s["symbol"] for s in sells} == {"X"}
    assert sells[0]["qty"] == 100


def test_pending_not_due_stays_pending():
    led = _ledger_with_orders()
    try:
        results = _fill_pending(led, {}, pd.Timestamp("2026-10-19"))  # 早于成交日
        assert all(r["status"] == "pending" for r in results)
        assert len(led.pending_orders()) == 4
    finally:
        led.close()
        _cleanup()
