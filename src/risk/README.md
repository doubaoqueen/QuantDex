# src/risk/ — 组合级风控

## 规则（`risk.py`）

| 函数 | 规则 |
|---|---|
| `cap_weights(w, max_single, max_total)` | 单票上限截断（超出转现金，不重分配）；总仓位超过 max_total 时整体缩放 |
| `drawdown_multiplier(nav, max_dd, scale)` | 回撤断路器：当前回撤深于 max_dd（如 -15%）时，仓位乘 scale（如 0.5） |
| `apply_risk(weights_df, nav, ...)` | 对目标权重矩阵逐日生效：先单票/总仓位截断，再按当日回撤缩放 |

## 接入点

```
策略生成目标权重 W ──► risk.apply_risk(W, nav=当前净值) ──► 组合回测 / 模拟盘下单
```

## 测试

`python tests/run_tests.py` 中的 `test_risk.py` 覆盖：满仓单票被压到上限、回撤未达阈值不触发、触发后按比例降仓。

## 后续

- 行业暴露上限（需先补行业映射数据）；
- 下单前资金/持仓可用性校验（接入模拟盘账本后实现）。
