# src/strategies/ — 策略层

> 策略只负责**生成信号**（0/1 仓位或目标仓位），不负责成交、费用与净值——那些由 `src/backtest/` 处理。

## 现有策略

| 文件 | 策略 | 逻辑 |
|---|---|---|
| `dual_ma.py` | 双均线择时 | 快线在慢线上方持仓，否则空仓；信号 T+1 执行（`position` 后移一日） |

## 新增策略规范

1. 输入标准化日线 DataFrame（含 date/open/high/low/close/volume）；
2. 输出同一 DataFrame，新增：
   - `raw_signal`：当日收盘信号（0/1 或目标仓位）；
   - `position`：**信号后移一日**（T+1 开盘执行，避免未来函数）；
3. 必须能通过 `src/backtest/engine.run_backtest` 直接用 `position` 列回测。

## 计划中

- `mean_reversion.py`：布林带 / z-score 均值回归（z<-2 做多、z>2 反向、回归 0 平仓，带硬止损）——P1 任务。
