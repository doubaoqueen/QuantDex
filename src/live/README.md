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

## 每日流水线 `pipeline.py`（MVP 已实现）

```bash
python scripts/08_daily_pipeline.py            # 正常运行（首次运行自动建仓）
python scripts/08_daily_pipeline.py --dry-run  # 只计算不落账本
```

流程：本地数据 → 目标权重（低换手+低波+短反转，每 20 交易日调仓）→
风控（单票 10% 上限 / 总仓 1.0 / 回撤断路器 -15%×0.5）→ 模拟成交（**收盘价**，MVP 简化）→
账本记录（快照/持仓/成交，同日重跑幂等）→ 日报 `data/store/reports/YYYY-MM-DD.md`。

约定：
- 非调仓日只做净值快照；空账户首次运行自动"建仓"（bootstrap）；
- 数据更新不在本模块内（先跑 02/04）；真实执行应为 T+1 开盘（账本 fill_date 字段已预留）。
