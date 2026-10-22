# -*- coding: utf-8 -*-
"""模拟盘账本回归测试：建表、写入、读回、WAL。"""
from pathlib import Path

import pandas as pd

from src.live.ledger import Ledger

DB = Path("data/store/sim/_test_ledger.sqlite")


def _cleanup():
    for suffix in ("", "-wal", "-shm"):
        Path(str(DB) + suffix).unlink(missing_ok=True)


def test_ledger_record_and_readback():
    _cleanup()
    try:
        led = Ledger(DB)
        positions = pd.DataFrame({
            "symbol": ["600519", "000001"],
            "shares": [100.0, 200.0],
            "close": [1500.0, 10.0],
            "weight": [0.75, 0.25],
        })
        trades = pd.DataFrame({
            "signal_date": ["2026-10-19"], "fill_date": ["2026-10-20"],
            "symbol": ["600519"], "side": ["buy"], "qty": [100.0],
            "price": [1500.0], "amount": [150000.0], "fee": [37.5],
            "stamp_duty": [0.0], "status": ["filled"], "note": ["test"],
        })
        led.record_day("2026-10-20", cash=500000.0, market_value=150000.0,
                       positions=positions, trades=trades)
        snap = led.snapshot()
        pos = led.positions("2026-10-20")
        tr = led.trades()
        assert len(snap) == 1
        assert abs(float(snap.iloc[0]["total_nav"]) - 650000.0) < 1e-6
        assert len(pos) == 2
        assert set(pos["symbol"]) == {"600519", "000001"}
        assert len(tr) == 1
        assert tr.iloc[0]["status"] == "filled"
        assert tr.iloc[0]["side"] == "buy"
        mode = led.connect().execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal"
    finally:
        try:
            led.close()
        except NameError:
            pass
        _cleanup()


def test_daily_ops_is_deduped():
    """每日操作记录：同一天同一账户同一动作只保留 1 行。"""
    _cleanup()
    try:
        led = Ledger(DB)
        led.record_daily_op("2026-10-22", "large", "pipeline", "done", "第一次")
        led.record_daily_op("2026-10-22", "large", "pipeline", "done", "第二次覆盖")
        led.record_daily_op("2026-10-23", "large", "pipeline", "done", "另一天")
        ops = led.daily_ops("large")
        assert len(ops) == 2
        row = ops[ops["date"] == "2026-10-22"].iloc[0]
        assert "第二次覆盖" in row["summary"]
    finally:
        try:
            led.close()
        except NameError:
            pass
        _cleanup()
