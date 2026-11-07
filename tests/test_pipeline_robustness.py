"""执行链健壮性回归（D-062）：退市/长缺数据 pending 解锁 + 滞留订单自检。

背景（审计 F-23）：行情末日 < fill_date 的 pending 永远"保持待成交"→
pending 永不为空 → 下单 guard 永久拦截新订单，账户无声停摆。
D-062 规则：成交日已过 30 个自然日仍无行情 → 疑似退市，拒单解锁。
"""
from pathlib import Path

import pandas as pd

from src.live.ledger import Ledger
from src.live.pipeline import _fill_pending, _self_check

DB = Path("data/store/sim/_test_pipeline_robust.sqlite")
D = pd.Timestamp("2026-10-20")


def _cleanup():
    for suffix in ("", "-wal", "-shm"):
        Path(str(DB) + suffix).unlink(missing_ok=True)


def _order(sym, fill_date, signal="2026-10-08"):
    return {"signal_date": signal, "fill_date": str(fill_date), "symbol": sym,
            "side": "buy", "qty": 100, "price": 10.0,
            "amount": 1000.0, "fee": 5.0, "stamp_duty": 0.0,
            "status": "pending", "note": "ref"}


def test_stale_pending_rejected_as_delisted():
    """成交日已过 30 天且无行情 → rejected_delisted（解锁下单链路）。"""
    _cleanup()
    try:
        led = Ledger(DB)
        # 疑似退市：成交日 = latest - 40 天，行情末日更早
        led.add_pending(pd.DataFrame([_order("600999", (D - pd.Timedelta(days=40)).date())]))
        # 正常延迟：成交日 = latest - 5 天，行情未到（保持 pending）
        led.add_pending(pd.DataFrame([_order("600519", (D - pd.Timedelta(days=5)).date())]))
        res = _fill_pending(led, price_lookup={}, latest=D,
                            last_dates={"600999": "2026-09-01",
                                        "600519": "2026-10-01"})
        by_sym = {r["symbol"]: r["status"] for r in res}
        assert by_sym["600999"] == "rejected_delisted", by_sym
        assert by_sym["600519"] == "pending", by_sym  # 未超期：保持待成交
        assert led.pending_orders()["symbol"].tolist() == ["600519"]
        led.close()
    finally:
        _cleanup()


def test_self_check_flags_stale_pending():
    """滞留 pending 是第四项自检：出现即 ✗ 且 ok=False（进邮件标题/退出码）。"""
    _cleanup()
    db2 = Path("data/store/sim/_test_pipeline_robust2.sqlite")
    try:
        led = Ledger(DB)
        led.add_pending(pd.DataFrame([_order("600999", (D - pd.Timedelta(days=40)).date())]))
        lines, ok = _self_check(led, cash=1000.0,
                                positions=pd.DataFrame())
        txt = "\n".join(lines)
        assert ok is False, txt
        assert "✗ 滞留待成交" in txt and "600999" in txt, txt
        led.close()
        # 无滞留时必须显式报 ✓（在途订单数可见）——独立账本，避免串数据
        for suffix in ("", "-wal", "-shm"):
            Path(str(db2) + suffix).unlink(missing_ok=True)
        led2 = Ledger(db2)
        led2.add_pending(pd.DataFrame([_order("600519", (D + pd.Timedelta(days=1)).date())]))
        lines2, ok2 = _self_check(led2, cash=1000.0, positions=pd.DataFrame())
        txt2 = "\n".join(lines2)
        assert ok2 is True and "✓ 无滞留待成交（在途 1 笔）" in txt2, txt2
        led2.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(db2) + suffix).unlink(missing_ok=True)
    finally:
        _cleanup()
