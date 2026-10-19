# src/data/ — 数据层

> 负责从外部数据源抓取行情、估值、财务、资金流、宏观数据，并统一标准化后写入 Parquet 数据仓库。

## 模块

| 文件 | 职责 |
|---|---|
| `sources.py` | baostock 登录单例（**多线程安全，加锁串行化**）、结果集转 DataFrame、带退避重试 `retry_call`（退市/无数据等确定性失败不重试） |
| `storage.py` | `ParquetStore`：分区读写、按 key 增量 upsert、注册表（registry）、进程内读缓存、`batch_write` 批量注册表缓冲 |
| `schema.py` | 6 位代码转换（含北交所 `bj.`）、各数据源中文列名映射、标准化函数、`kline_file_name()` |
| `universe.py` | 股票/ETF/指数/可转债列表、交易日历、全量证券、行业分类、指数成分股、**PIT 历史成分**（`load_pit_constituents` / `pit_union_symbols`） |
| `market.py` | 日/周/月/分钟线、实时快照、批量增量更新（线程池、失败清单） |
| `valuation.py` | 个股 PE/PB/PS/市值（百度）、指数 PE/PB（乐咕） |
| `financials.py` | 财务摘要 + baostock 季频六件套 + 快报/预告 + 分红 + 复权因子（**披露日 pub_date 与报告期 report_date 分离**） |
| `moneyflow.py` | 北向资金、两融、龙虎榜 |
| `macro.py` | CPI、LPR、货币供应、利率、存款准备金率 |
| `fetch_baostock.py` / `loader.py` | 【legacy】初版一次性脚本，仅存档 |

## 关键约定

1. **复权口径隔离**：行情落盘为 `data/store/market/daily/{symbol}__{qfq|hfq|none}.parquet`，同标的、不同口径**分文件存放，禁止混写**；
2. **行级来源**：行情标准列自带 `source`（akshare/em、akshare/sina、baostock），可审计每条数据的出处；
3. **增量更新**：`update_kline_batch` 对已有数据只抓「本地最后日期+1 → 今天」的增量；已覆盖最近收盘的标的直接跳过（依赖 `meta/trade_calendar`）；
4. **并发边界**：东财/新浪可多线程抓取（默认 `workers=4`，过高会触发限流；个股东财失败自动降级新浪）；**baostock 单连接协议，必须经 `sources.bs_query` 串行化调用**，否则并发会损坏数据流；
5. **退市股**：东财/新浪无数据，用 `fetch_kline(source="bs")` 走 baostock 补齐。

## 使用示例

```python
from src.data.storage import ParquetStore
from src.data import market, universe

store = ParquetStore()
market.update_kline_batch(["600519", "000300"], asset="stock", store=store)
df = market.get_kline("600519", adjust="qfq", store=store)   # 指数用 adjust="none"

symbols = universe.pit_union_symbols("000300", store)        # 沪深300 历史成分并集
pit = universe.load_pit_constituents("000300", store)        # in_date/out_date 区间表
```
