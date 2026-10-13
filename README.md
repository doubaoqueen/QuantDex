# QuantDex

> A 股量化交易工具：多源数据抓取 → Parquet 数据仓库 → 数据质检 / 技术指标 / 分析报告 → 策略回测。
> Rookie makes its way into the A-share market.

当前版本 **v0.2**：在最初「单只 ETF 日线 + 双均线」demo 的基础上，重构了**数据层与分析层**，
对标个人量化框架（akshare / baostock 生态）的数据标准，数据可直接用于后续选股、择时与研究。

---

## 一、能力总览

| 维度 | 支持内容 |
| --- | --- |
| 数据源 | **akshare**（东财/新浪聚合，免 token）、**baostock**（免费证券数据平台）、**百度股市通** |
| 品种 | A 股个股、ETF、指数、可转债 |
| K 线周期 | 日 / 周 / 月线、分钟线（1/5/15/30/60）、全市场实时快照 |
| 复权 | 前复权 qfq / 后复权 hfq / 不复权；复权因子历史 |
| 标的池 | 股票 / ETF / 指数 / 可转债列表、交易日历、全量证券（含 IPO/退市）、行业分类、指数成分股 |
| 估值 | 个股 PE(TTM)/PB/PS(TTM)/总市值历史；指数 PE/PB 历史 |
| 财务 | 财务摘要（80+ 指标）、季频六件套（盈利/营运/成长/偿债/现金流/杜邦）、业绩快报/预告、分红送配 |
| 资金面 | 北向资金、沪深两市融资融券、龙虎榜 |
| 宏观 | CPI、LPR、货币供应量、存贷款基准利率、存款准备金率 |
| 存储 | Parquet 分区存储 + 增量更新（upsert）+ 数据注册表 |
| 分析 | 技术指标库、数据质量检查、收益/风险概览、分年度收益、仓库覆盖报告 |
| 回测 | T+1 开盘成交、佣金/滑点/印花税、净值与回撤、完整绩效、逐笔交易明细 |

---

## 二、目录结构

```
QuantDex/
├── config.py                     # 全局配置：数据目录、默认参数
├── requirements.txt
├── data/
│   ├── raw/                      # 旧版裸 CSV（保留）
│   └── store/                    # Parquet 数据仓库
│       ├── market/
│       │   ├── daily/{symbol}.parquet
│       │   ├── weekly/  monthly/
│       │   └── minute/{symbol}__{freq}.parquet
│       ├── meta/                 # 列表、日历、成分、行业等
│       ├── valuation/            # 个股 / 指数估值
│       ├── financials/
│       │   ├── abstract/{symbol}.parquet
│       │   └── baostock/{symbol}__{类型}.parquet
│       ├── moneyflow/            # 北向 / 两融 / 龙虎榜
│       ├── macro/                # 宏观
│       └── registry.parquet      # 数据注册表
├── src/
│   ├── data/
│   │   ├── storage.py            # ParquetStore：读写/增量/注册表
│   │   ├── sources.py            # baostock 登录单例、重试
│   │   ├── schema.py             # 代码转换、列名映射、标准化
│   │   ├── universe.py           # 列表/日历/成分/行业
│   │   ├── market.py             # K线/分钟线/快照/批量更新
│   │   ├── valuation.py          # 个股/指数估值
│   │   ├── financials.py         # 财务/分红/复权因子
│   │   ├── moneyflow.py          # 北向/两融/龙虎榜
│   │   ├── macro.py              # 宏观
│   │   ├── fetch_baostock.py     # [legacy] 初版一次性脚本
│   │   └── loader.py             # [legacy] 初版 CSV 读取
│   ├── analysis/
│   │   ├── indicators.py         # 技术指标库
│   │   ├── quality.py            # 数据质检
│   │   └── report.py             # 分析报告
│   ├── backtest/
│   │   ├── engine.py             # 向量化回测引擎
│   │   └── metrics.py            # 绩效指标 / 交易明细
│   └── strategies/
│       └── dual_ma.py            # 双均线策略
└── scripts/                      # 一键脚本（按编号顺序运行）
    ├── 01_init_universe.py
    ├── 02_update_market.py
    ├── 03_fetch_alternative.py
    └── demo_analysis.py
```

---

## 三、安装

推荐使用已有的 conda 环境 `QuantDex`（Python 3.11）：

```powershell
# 激活环境
conda activate QuantDex

# 安装依赖（国内用清华镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 四、三步上手（在项目根目录运行）

```powershell
# 第 1 步：初始化标的池（列表/日历/成分/行业，约 1 分钟）
python scripts/01_init_universe.py

# 第 2 步：批量抓取核心标的日线（可反复运行，自动增量）
python scripts/02_update_market.py

# 第 3 步：抓取估值/财务/资金流/宏观/龙虎榜
python scripts/03_fetch_alternative.py

# 分析演示：质检 → 概览 → 分年度 → 回测（可传代码参数）
python scripts/demo_analysis.py
python scripts/demo_analysis.py 600519
```

第 2、3 步都可**重复运行**：已有数据只抓取最新部分（upsert 按日期去重），不会重复堆积。

---

## 五、数据 Schema（标准化后）

全系统内部统一使用 **6 位纯代码**（如 `600519` / `510300` / `000300`）；
调用 baostock 时由 `schema.to_bs_code()` 自动加 `sh.` / `sz.` 前缀。

**日 / 周 / 月线**（`market/daily/{symbol}.parquet`）：

| 列 | 含义 |
| --- | --- |
| symbol | 6 位代码 |
| date | 交易日期 |
| open / high / low / close | 开 / 高 / 低 / 收 |
| volume | 成交量（股 / 份） |
| amount | 成交额（元） |
| amplitude | 振幅（%） |
| pct_chg | 涨跌幅（%） |
| change | 涨跌额 |
| turnover | 换手率（%） |

**分钟线**（`market/minute/{symbol}__{freq}.parquet`）：把 `date` 换成 `datetime`，其余相同。

**财务（baostock 季频）**：`financials/baostock/{symbol}__{类型}.parquet`，类型为
`profit`（盈利：ROE/净利率/毛利率/EPS/总股本）、`operation`（营运）、`growth`（成长）、
`balance`（偿债/资产负债，含净资产）、`cashflow`（现金流）、`dupont`（杜邦）、
`dividend`（分红）、`adjfactor`（复权因子）、`express`（快报）、`forecast`（预告）。

**金额单位注意**：北向资金为 **亿元**；两融汇总为 **万元**（数据源原始口径）。

---

## 六、作为库调用（API 速查）

```python
from src.data.storage import ParquetStore
from src.data import universe, market, valuation, financials, moneyflow, macro
from src.analysis.indicators import add_indicators
from src.analysis.quality import quality_report
from src.analysis.report import symbol_overview, store_coverage

store = ParquetStore()

# 行情：抓 / 读
market.fetch_daily("600519", start="20200101", asset="stock", adjust="qfq", store=store)
df = market.get_kline("600519", store=store)          # 读本地
market.fetch_minute("600519", freq="5", store=store)  # 分钟线
spot = market.fetch_spot("stock", store=store)        # 实时快照

# 批量增量更新
market.update_kline_batch(["600519", "000858"], asset="stock", store=store)

# 估值 / 财务 / 资金 / 宏观
valuation.fetch_stock_valuation("600519", store=store)
financials.fetch_all_financials("600519", years=3, store=store)
moneyflow.fetch_northbound("北向资金", store=store)
moneyflow.fetch_margin_hist("sh", store=store)
macro.fetch_bs_macro(store)

# 分析
ind = add_indicators(df)                    # 全套技术指标
qsum, details = quality_report(df, store)   # 质检（明细在 details）
ov = symbol_overview(df)                    # 收益/风险概览
print(store_coverage(store))                # 仓库覆盖
```

回测：

```python
from src.strategies.dual_ma import dual_ma_signal
from src.backtest.engine import run_backtest
from src.backtest.metrics import performance

sig = dual_ma_signal(df, fast=5, slow=20)
result, trades = run_backtest(sig, fee=0.00025, slippage=0.0005,
                              stamp_duty=0.0)      # 股票卖出印花税填 0.0005
stats = performance(result, trades)
```

---

## 七、技术指标清单

`analysis/indicators.py`（纯 pandas/numpy，无额外依赖）：
MA（5/10/20/60/120/250）、EMA、MACD（DIF/DEA/MACD柱）、RSI（6/12/24）、
KDJ、布林带 BOLL、ATR、威廉 WR、CCI、OBV、动量 MOM、PSY；
`add_indicators(df)` 一次全部计算。

---

## 八、已知边界与后续方向

- baostock 0.9.4 无 CPI/PPI/GDP/PMI 接口：CPI 由 akshare 提供；PPI/GDP/PMI 可按需接其他源。
- 分钟线由数据源返回最近约 1–2 个月，非全历史；实时快照默认不落盘（`save=True` 可存）。
- 北向资金 2024 年后披露口径调整，接口保留历史数据。
- 回测为**向量化、单标的、0/1 仓位**模型，适合择时验证；未做逐笔撮合与多标的组合。

后续可扩展：① 多标的组合 / 截面选股回测；② 事件驱动逐笔引擎；
③ 因子库与 IC/分层检验；④ 定时任务每日自动更新；⑤ 策略参数寻优。



## 免责声明

本项目仅供学习与研究使用，不构成任何投资建议。
作者不对使用本工具产生的任何盈亏负责。
