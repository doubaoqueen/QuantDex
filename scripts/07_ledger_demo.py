# -*- coding: utf-8 -*-
"""
模拟盘账本演示：写入一条当日示例记录（快照 + 持仓 + 成交）并做每日备份。

用法: python scripts/07_ledger_demo.py
数据库: data/store/sim/ledger.sqlite（WAL 模式，已加入 .gitignore）
"""
import datetime

import _bootstrap  # noqa: F401
import pandas as pd

from src.live.ledger import Ledger


def main() -> None:
    led = Ledger()
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

    p = led.daily_backup()
    print("备份:", p)
    led.close()


if __name__ == "__main__":
    main()
