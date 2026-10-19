# src/analysis/ — 分析层

> 技术指标、因子研究、数据质检、报告与绘图。输入标准化行情 DataFrame，输出可检验的结论与图片。

## 模块

| 文件 | 职责 | 关键接口 |
|---|---|---|
| `indicators.py` | 技术指标库（纯 pandas/numpy） | `add_indicators(df)` 一键全套；MA/EMA/MACD/RSI/KDJ/BOLL/ATR/WR/CCI/OBV/MOM/PSY |
| `factors.py` | 因子库：8 个量价因子 + 截面面板 | `compute_raw_factors(df)`；`build_factor_panel(store, symbols, adjust="qfq", pit_constituents=...)` |
| `factor_test.py` | 因子有效性检验引擎 | `run_factor_tests(panel)`：RankIC / ICIR / **Newey-West t 值** / 衰减 / 分层回测（算术+中位数双口径、含换手成本） |
| `quality.py` | 数据质检 | `quality_report(df, store)`：重复/缺日/零成交/OHLC 逻辑/异常涨跌 |
| `report.py` | 研究报告 | `symbol_overview`（收益/风险/基准）、`yearly_returns`、`store_coverage`、`market_breadth` |
| `plot.py` | 静态出图（Pillow，PNG） | 5 张核心图：行情+均线、回测净值+回撤、IC 热力图、分层净值、分年度收益 |

## 关键说明

1. **PIT 过滤**：`build_factor_panel(..., pit_constituents=pit)` 只保留「当天真实在指数内」的 (date, symbol)，消除幸存者偏差；04 脚本默认启用；
2. **因子检验口径**：RankIC 用日频截面相关；显著性看 `nw_tstat`（重叠持有期自相关一致，|t|>2 才显著），不要只看普通 t 值；
3. **绘图为什么用 Pillow**：本环境（pandas 3.x + numpy 2.x / Windows）matplotlib 的 Agg 渲染存在原生崩溃，Pillow 路径经完整验证；图默认存 `data/store/plots/`；
4. **研究原则**：任何因子结论必须回答——有没有关系（IC）、稳不稳（ICIR/NW t）、能撑几天（衰减）、分组单调吗（分层回测）。

## 使用示例

```python
from src.data.storage import ParquetStore
from src.data import universe, market
from src.analysis.factors import build_factor_panel
from src.analysis.factor_test import run_factor_tests
from src.analysis.plot import plot_ic_heatmap

store = ParquetStore()
pit = universe.load_pit_constituents("000300", store)
panel = build_factor_panel(store, universe.pit_union_symbols("000300", store),
                           start="2018-01-01", pit_constituents=pit)
res = run_factor_tests(panel)          # ic_table / quantile 分层结果
plot_ic_heatmap(res["ic_table"])       # data/store/plots/ic_heatmap.png
```
