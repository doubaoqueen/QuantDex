# data/ — Parquet 数据仓库

> 所有抓取数据的本地持久化目录。2026-10-23 起行情改为**按日分区入库**（append-only），
> `market/daily/` 的每股票文件仅作**本地读缓存**（gitignore，可重建）。

## 目录结构

```
data/store/
├── registry.parquet     # 数据注册表：每个数据集的行数/日期范围/数据源/复权口径/更新时间
├── market/
│   ├── daily_by_date/YYYY-MM-DD.parquet    # 日线按日分区（git 入库；每天新增 1 个文件）
│   ├── daily/{symbol}__{adjust}.parquet    # 日线缓存（gitignore；克隆后 18 脚本重建）
│   ├── weekly/  monthly/                   # 周线/月线（同上命名）
│   └── minute/{symbol}__{freq}__{adjust}.parquet
├── meta/
│   ├── stock_list.parquet                  # 股票列表
│   ├── constituents_000300.parquet         # 指数当前成分（中证官网）
│   ├── pit_constituents_000300.parquet     # PIT 历史成分区间（in_date/out_date）
│   ├── pit_constituents_000905.parquet     # 中证500 PIT 历史成分
│   ├── trade_calendar.parquet              # 交易日历（增量跳过依赖）
│   ├── etf_list / index_list / bond_list / bs_securities / industry
│   └── spot_{stock|etf}.parquet            # 实时快照（可选落盘）
├── valuation/{symbol}.parquet              # 个股估值（PE/PB/PS/市值）
│   └── index_{名称}.parquet                # 指数估值
├── financials/abstract/{symbol}.parquet    # 财务摘要（长表）
├── financials/baostock/{symbol}__{类型}.parquet  # 季频六件套/快报/预告/分红/复权因子
├── moneyflow/                              # 北向/northbound、两融 margin_sh/sz、龙虎榜 lhb
├── macro/                                  # CPI/LPR/货币供应/利率/准备金
├── factor_results/                         # 04 脚本输出：ic_summary.parquet、nav_*.parquet
│   └── plots/                              # 自动生成的 PNG 图
└── plots/                                  # 通用出图（demo_analysis 等）
```

## 关键约定

1. **复权口径隔离**：qfq/hfq/none 三种口径数值不同，**同标的分文件存放、禁止混写**；`get_kline(sym, adjust=...)` 按口径读取（指数用 `none`）；
2. **行级来源**：行情标准列自带 `source`（akshare/em、akshare/sina、baostock）；
3. **增量更新**：按 key（symbol+date）upsert 去重，新数据优先；注册表由 `batch_write` 批量落盘；
4. **PIT 成分表**：`in_date <= date < out_date` 判定当日是否为指数成分；数据源已做三点外部核验（当前截面 300/300、2023-12 全量名单、2024-06/12 点名名单均一致）；
5. **财务表**：`pub_date`（披露日）与 `report_date`（报告期）分离——做基本面因子必须以 `pub_date` 对齐防前视偏差；
6. **金额单位**：北向资金为亿元；两融汇总为万元（数据源原始口径）。

## 注意

- 数据文件由脚本生成，**不要手动修改**；重建数据 = 删除对应 parquet 后重跑脚本；
- `registry.parquet` 是数据集元信息总表，可用 `store.registry()` 或 `report.store_coverage(store)` 查看覆盖情况。
