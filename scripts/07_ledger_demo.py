# -*- coding: utf-8 -*-
"""
模拟盘账本演示：写入一条当日示例记录（快照 + 持仓 + 成交）并做每日备份。

用法: python scripts/07_ledger_demo.py
**注意：演示使用独立的临时账本文件，绝不写入生产账本**
（data/store/sim/ledger.sqlite 只允许 08_daily_pipeline.py 写入）。
"""
import datetime
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.live.ledger import Ledger

DEMO_DB = STORE_DIR / "sim" / "_demo_ledger.sqlite"


def _cleanup() -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(str(DEMO_DB) + suffix).unlink(missing_ok=True)


def main() -> None:
    _cleanup()
    led = Ledger(DEMO_DB)   # 演示专用临时库
    today = datetime.date.today().isoformat()

    positions = pd.DataFrame({
        "symbol": ["600519", "000001"],
        "shares": [100.0, 1000.0],
        "close": [1490.0, 11.2],
        "weight": [0.67, 0.05],
    })
    trades = pd.DataFrame({
        "signal_date": [today], "fill_date": [today],
        "symbol": ["600519"], "side": ["buy"], "qty": [100.0],
        "price": [1490.0], "amount": [149000.0], "fee": [37.25],
        "stamp_duty": [0.0], "status": ["filled"], "note": ["演示记录"],
    })
    cash = 300000.0 - 149000.0 - 37.25
    market_value = 149000.0 + 11200.0

    led.record_day(today, cash=cash, market_value=market_value,
                   positions=positions, trades=trades)

    print("== 快照 ==")
    print(led.snapshot().tail(3).to_string(index=False))
    print("== 持仓 ==")
    print(led.positions(today).to_string(index=False))
    print("== 成交 ==")
    print(led.trades().tail(5).to_string(index=False))

    # 演示备份到临时路径（不写生产 backups/ 目录）
    import shutil
    led.connect().commit()
    bak = DEMO_DB.with_suffix(".backup.sqlite")
    shutil.copyfile(DEMO_DB, bak)
    print("演示备份:", bak)
    led.close()
    _cleanup()   # 删除演示库与演示备份，生产账本不受影响
    print("演示完成（临时账本已清理，生产账本 data/store/sim/ledger.sqlite 未被触碰）")


if __name__ == "__main__":
    main()
