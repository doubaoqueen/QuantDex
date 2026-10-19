# src/backtest/ — 回测层

> 向量化回测引擎与绩效指标。信号由策略层生成，本层只负责成交模拟、费用扣除与净值计算。

## 模块

| 文件 | 职责 | 关键接口 |
|---|---|---|
| `engine.py` | 向量化回测引擎 | `run_backtest(df, fee, slippage, stamp_duty)`：T+1 开盘成交、佣金/滑点/印花税、净值与回撤 |
| `metrics.py` | 绩效指标与逐笔交易 | `performance(result, trades)`：收益/波动/夏普/Sortino/Calmar/回撤/胜率/盈亏比/信息比率 |
| `portfolio.py` | 组合级回测引擎 | `run_portfolio_backtest(weights, prices)`：目标权重矩阵、T+1 生效、权重自然漂移、换手成本+印花税；`portfolio_performance` 绩效摘要 |

## 约定

- **T+1 开盘成交**：信号 T 日收盘生成，T+1 日开盘按开盘价执行（`price_col="open"`）；
- 输入 DataFrame 必须含 `position` 列（0/1 仓位，或未来扩展为权重）；
- 费用：佣金 `fee`（单边）、滑点 `slippage`（按成交额）、卖出印花税 `stamp_duty`（股票 0.0005，ETF 0）；
- 每笔交易明细由 `extract_trades` 提取（entry/exit 价、收益、持仓天数）。

## 已知边界（roadmap）

- 当前为 **单标的、0/1 仓位** 向量化模型；多标的组合/截面回测（目标权重矩阵、Top-N、定期调仓）是下一阶段重点（P0-2）；
- 未模拟涨跌停买不进、停牌无法交易；事件驱动撮合属远期能力。

## 使用示例

```python
from src.data.market import get_kline
from src.strategies.dual_ma import dual_ma_signal
from src.backtest.engine import run_backtest
from src.backtest.metrics import performance

df = get_kline("600519", adjust="qfq")
sig = dual_ma_signal(df, fast=5, slow=20)
result, trades = run_backtest(sig, fee=0.00025, slippage=0.0005, stamp_duty=0.0005)
print(performance(result, trades))
```
