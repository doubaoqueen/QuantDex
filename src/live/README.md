# src/live/ — 模拟盘（影子盘）

## 账本 `ledger.py`（SQLite，WAL）

| 表 | 字段 |
|---|---|
| `account_snapshot` | date(主键), cash, market_value, total_nav, benchmark_nav, created_at |
| `positions` | date, symbol, shares, close, weight（主键 date+symbol） |
| `trade_log` | id, signal_date, fill_date, symbol, side, qty, price, amount, fee, stamp_duty, status, note |

- `status`：filled / rejected_limit_up（涨停买不进）/ rejected_suspended（停牌）/ partial（部分成交）
- **signal_date 与 fill_date 分离**，用于核对"信号日→成交日"的时点；
- 数据库默认 `data/store/sim/ledger.sqlite`（已 gitignore，属操作状态）；`daily_backup()` 每日备份到 `backups/`，保留最近 30 份。

## 使用

```python
from src.live.ledger import Ledger
led = Ledger()
led.record_day("2026-10-20", cash=300000, market_value=160000,
               positions=pos_df, trades=trade_df, benchmark_nav=1.02)
print(led.snapshot().tail())
led.daily_backup()
```

演示：`python scripts/07_ledger_demo.py`
