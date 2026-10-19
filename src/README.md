# src/ — 核心代码分层

> 本目录按「数据 → 分析 → 回测 → 策略」四层组织，各层目录均有独立 README。

```
src/
├── data/       数据层：抓取、标准化、Parquet 仓库存储
├── analysis/   分析层：技术指标、因子研究、质检、报告、绘图
├── backtest/   回测层：向量化引擎、绩效指标
└── strategies/ 策略层：信号生成（只负责产生信号，不负责成交与费用）
```

## 数据流

```
数据源(akshare/baostock/百度)
   │  src/data/*         抓取 + 标准化 + 增量 upsert
   ▼
data/store/ (Parquet 仓库)
   │  src/analysis/*      指标 / 因子面板 / 质检 / 报告
   ▼
src/strategies/*         策略生成信号(position / 目标仓位)
   │
   ▼
src/backtest/*           成交模拟(T+1 开盘)、扣费、绩效
```

## 约定

- 全系统内部统一 **6 位纯代码**（600519 / 510300 / 000300），调用各方数据源时由 `schema.to_bs_code() / to_sina_code()` 转换；
- 行情文件按 `{symbol}__{adjust}` 命名（qfq/hfq/none 分文件隔离），注册表记录复权口径；
- 每层只依赖下层的公开接口，不反向依赖。
