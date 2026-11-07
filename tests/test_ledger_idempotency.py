"""下单幂等回归测试（D-059）：同一 (signal_date, symbol, side) 的 pending
在数据库层只允许存在一条——并发入口同时读到"pending 为空"再各写一批时，
第二批发写在唯一索引处被拒绝，而不是静默双份（D-053 同类指纹的预防针）。
"""
from pathlib import Path

import pandas as pd

from src.live.ledger import Ledger

DB = Path("data/store/sim/_test_ledger_idem.sqlite")


def _cleanup():
    for suffix in ("", "-wal", "-shm"):
        Path(str(DB) + suffix).unlink(missing_ok=True)


def _order(symbol="600519", side="buy", price=1500.0, signal="2026-10-19"):
    qty = 100
    return {"signal_date": signal, "fill_date": "2026-10-20", "symbol": symbol,
            "side": side, "qty": qty, "price": price, "amount": qty * price,
            "fee": 37.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"}


def test_add_pending_rejects_duplicate_batch():
    """同一批订单写两遍（并发双入口形态）：第二遍必须 0 条写入、总数不变。"""
    _cleanup()
    try:
        led = Ledger(DB)
        batch = pd.DataFrame([_order(), _order("000001", "sell", 12.0)])
        n1 = led.add_pending(batch)
        assert n1 == 2
        n2 = led.add_pending(batch)  # 模拟第二个进程重复下单
        assert n2 == 0, "重复批次必须被防重索引整体拒绝"
        assert len(led.pending_orders()) == 2, "账本里不能出现双份 pending"
        led.close()
    finally:
        _cleanup()


def test_add_pending_partial_overlap():
    """半重叠批次：重复的那只被忽略，新标的正常写入（不误伤正常下单）。"""
    _cleanup()
    try:
        led = Ledger(DB)
        assert led.add_pending(pd.DataFrame([_order()])) == 1
        overlap = pd.DataFrame([_order(), _order("000858", "buy", 150.0)])
        n = led.add_pending(overlap)
        assert n == 1, "只有新标的被写入"
        assert len(led.pending_orders()) == 2
        led.close()
    finally:
        _cleanup()


def test_add_pending_unblocked_after_fill():
    """成交/拒单后解除占用：次日同标的同方向可再次下单（防重不挡正常交易）。"""
    _cleanup()
    try:
        led = Ledger(DB)
        assert led.add_pending(pd.DataFrame([_order()])) == 1
        pend = led.pending_orders()
        led.mark_filled(int(pend["id"].iloc[0]), "2026-10-20", 1501.0,
                        150100.0, 37.5, 0.0)
        # 次一交易日新一批（signal_date 不同）不被拦截
        nxt = pd.DataFrame([_order(signal="2026-11-03")])
        assert led.add_pending(nxt) == 1
        # 同 signal_date 重复仍被拒
        assert led.add_pending(nxt) == 0
        led.close()
    finally:
        _cleanup()
