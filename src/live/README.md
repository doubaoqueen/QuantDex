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

## 每日流水线 `pipeline.py`（v2：T+1 两段式成交）

```bash
python scripts/08_daily_pipeline.py            # 正常运行
python scripts/08_daily_pipeline.py --dry-run  # 只计算不落账本
```

**成交口径（与回测一致，无未来函数）**：
1. 信号日 T 收盘后：算目标权重（低换手+低波+短反转，每 20 交易日调仓）→
   过风控（单票 10% / 总仓 1.0 / 回撤断路器 -15%×0.5）→ 生成订单记为 `pending`，
   `fill_date` = 下一交易日；
2. T+1 收盘后运行：按 **T+1 开盘价**撮合 pending 订单——
   开盘涨停买不进 → `rejected_limit_up`；跌停卖不出 → `rejected_limit_down`；
   停牌/无行情 → `rejected_suspended`；正常 → `filled`（写真实成交价与费用）；
3. 快照 + 持仓（最新收盘价）+ 日报 `data/store/reports/YYYY-MM-DD.md`。

实测验证（2026-10-19 → 10-20）：49 笔挂单 → 次日在 10-20 开盘价全部成交，
净值 993,674.68（含成本与当日浮亏），同日重跑零重复。

约定：非调仓日只做净值快照；空账户首次运行自动"建仓"（挂单等次日开盘）；
数据更新不在本模块（先跑 02/04）。

## 邮件推送 `notifier.py`（可选）

- SMTP 纯文本邮件（QQ 邮箱示例：`smtp.qq.com:465` SSL）；
- 配置来源：环境变量 > 项目根 `.env`（**`.env` 已被 .gitignore 忽略，禁止提交**）：
  `SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS / REPORT_TO`；
- 流水线在生成日报后自动发送（未配置或失败只打印提示，不影响记账）；`08 --no-push` 可关闭；
- 测试：`python scripts/09_test_mail.py`。

## 双账户（2026-10-20 起）

| 账户 | 账本 | 日报目录 | 资金 | 策略口径 |
|---|---|---|---|---|
| **large**（看大盘） | `data/store/sim/ledger.sqlite` | `reports/large/` | 100 万 | Top50 等权，每 20 交易日调仓 |
| **small**（实盘建仓模拟） | `data/store/sim/ledger_2000.sqlite` | `reports/small/` | **2000 元** | 整手小组合：按分数挑**买得起 1 手**的标的，最多 3 只 |

**2000 元账户的特殊处理**：A 股 100 股一手，2000 元做不了 50 只等权（每只 40 元）。
因此 `_plan_small_orders` 按分数顺序挑「一手成本 ≤ 可用现金」的标的，每只买 1 手，
最多 `max_positions` 只；未选中的持仓全部卖出。**代价是集中度高（每只约占 25%~50%）、
且只能买低价股（价格越高越容易买不起）**——这是小资金实盘的客观约束，需在归因报告里明示。

启动命令：
```bash
# large
python scripts/08_daily_pipeline.py
# small
python scripts/08_daily_pipeline.py --capital 2000 --db data/store/sim/ledger_2000.sqlite --label small --max-positions 3
```
每日一键 `10_daily_run.py` 会依次跑两个账户。
