# QuantDex

> A 股量化交易工具：多源数据抓取 → Parquet 数据仓库 → 数据质检 / 技术指标 / 分析报告 → 策略回测。
> Rookie makes its way into the A-share market.

当前版本 **v0.2**：在最初「单只 ETF 日线 + 双均线」demo 的基础上，重构了**数据层与分析层**，

对标个人量化框架（akshare /baostock 生态）的数据标准，数据可直接用于后续选股、择时与研究。



***

## 一、能力总览



| 维度    | 支持内容                                                            |
| ----- | --------------------------------------------------------------- |
| 数据源   | **akshare**（东财 / 新浪聚合，免 token）、**baostock**（免费证券数据平台）、**百度股市通** |
| 品种    | A 股个股、ETF、指数、可转债                                                |
| K 线周期 | 日 / 周 / 月线、分钟线（1/5/15/30/60）、全市场实时快照                            |
| 复权    | 前复权 qfq / 后复权 hfq / 不复权；复权因子历史                                  |
| 标的池   | 股票 / ETF / 指数 / 可转债列表、交易日历、全量证券（含 IPO / 退市）、行业分类、指数成分股          |
| 估值    | 个股 PE (TTM)/PB/PS (TTM)/ 总市值历史；指数 PE/PB 历史                      |
| 财务    | 财务摘要（80+ 指标）、季频六件套（盈利 / 营运 / 成长 / 偿债 / 现金流 / 杜邦）、业绩快报 / 预告、分红送配 |
| 资金面   | 北向资金、沪深两市融资融券、龙虎榜                                               |
| 宏观    | CPI、LPR、货币供应量、存贷款基准利率、存款准备金率                                    |
| 存储    | Parquet 分区存储 + 增量更新（upsert）+ 数据注册表                              |
| 分析    | 技术指标库、数据质量检查、收益 / 风险概览、分年度收益、仓库覆盖报告、**可视化出图（PNG）**       |
| 因子    | 8 类量价因子库、**RankIC / ICIR / 衰减**、**分层回测（算术 + 中位数双口径）**           |
| 回测    | T+1 开盘成交、佣金 / 滑点 / 印花税、净值与回撤、完整绩效、逐笔交易明细                        |



***

## 二、目录结构



```
QuantDex/
├── config.py                     # 全局配置：数据目录、默认参数
├── requirements.txt
├── data/
│   ├── raw/                      # 旧版裸 CSV（保留）
│   └── store/                    # Parquet 数据仓库
│       ├── market/
│       │   ├── daily/{symbol}__{adjust}.parquet    # adjust: qfq/hfq/none（复权口径分文件隔离）
│       │   ├── weekly/  monthly/                    # 命名同上
│       │   └── minute/{symbol}__{freq}__{adjust}.parquet
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
│   │   ├── factors.py            # 因子库：量价因子计算 / 面板构建
│   │   ├── factor_test.py        # 因子检验：RankIC/ICIR/衰减/分层回测
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
    ├── 04_factor_research.py     # 因子抓取 + 检验
    └── demo_analysis.py
```



***

## 三、安装

推荐使用已有的 conda 环境 `QuantDex`（Python 3.11）：



```
# 安装环境（建议 python 3.11）
conda create -n QuantDex python=3.11 -y

# 激活环境
conda activate QuantDex

# 安装依赖（国内用清华镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```



***

## 四、三步上手（在项目根目录运行）



```
# 第 0 步：下载历史指数成分表（point-in-time，消除幸存者偏差）
python scripts/00_fetch_pit_constituents.py

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

第 0 步下载 **point-in-time 历史指数成分表**（消除幸存者偏差的地基数据）；
第 2、3 步都可**重复运行**：已有数据只抓取最新部分（upsert 按日期去重），不会重复堆积。



***

## 五、数据 Schema（标准化后）

全系统内部统一使用 **6 位纯代码**（如 `600519` / `510300` / `000300`）；

调用 baostock 时由 `schema.to_bs_code()` 自动加 `sh.` / `sz.` / `bj.`（北交所）前缀。

**日 / 周 / 月线**（`market/daily/{symbol}__{adjust}.parquet`，adjust 为 qfq/hfq/none）：



| 列                         | 含义            |
| ------------------------- | ------------- |
| symbol                    | 6 位代码         |
| date                      | 交易日期          |
| open / high / low / close | 开 / 高 / 低 / 收 |
| volume                    | 成交量（股 / 份）    |
| amount                    | 成交额（元）        |
| amplitude                 | 振幅（%）         |
| pct\_chg                  | 涨跌幅（%）        |
| change                    | 涨跌额           |
| turnover                  | 换手率（%）        |
| source                    | 行级数据源（akshare/em、akshare/sina、baostock） |

> 复权口径（qfq/hfq/none）记在文件名与注册表 adjust 列：同标的、不同口径的数据落在不同文件，禁止混写。source 列保留每条记录出处，跨源 upsert 时可审计。get_kline 用 adjust 参数指定口径（默认 qfq，指数用 none）。

**分钟线**（`market/minute/{symbol}__{freq}__{adjust}.parquet`）：把 `date` 换成 `datetime`，其余相同。

**财务（baostock 季频）**：`financials/baostock/{symbol}__{类型}.parquet`，类型为

`profit`（盈利：ROE / 净利率 / 毛利率 / EPS / 总股本）、`operation`（营运）、`growth`（成长）、

`balance`（偿债 / 资产负债，含净资产）、`cashflow`（现金流）、`dupont`（杜邦）、

`dividend`（分红）、`adjfactor`（复权因子）、`express`（快报）、`forecast`（预告）。

财务表已标准化：统一携带 `symbol`（6 位）与 `bs_code`（sh./sz. 前缀），披露日统一为 `pub_date`、报告期统一为 `report_date`。做基本面因子时必须按 `pub_date`（当时已披露）对齐，而非 `report_date`，否则引入前视偏差。

**金额单位注意**：北向资金为 **亿元**；两融汇总为 **万元**（数据源原始口径）。



***

## 六、作为库调用（API 速查）



```
from src.data.storage import ParquetStore
from src.data import universe, market, valuation, financials, moneyflow, macro
from src.analysis.indicators import add_indicators
from src.analysis.quality import quality_report
from src.analysis.report import symbol_overview, store_coverage

store = ParquetStore()

# 行情：抓 / 读
market.fetch_daily("600519", start="20200101", asset="stock", adjust="qfq", store=store)
df = market.get_kline("600519", adjust="qfq", store=store)   # 读本地（指数用 adjust="none"）
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

# 可视化（PNG 静态图，默认存 data/store/plots/，返回文件路径）
from src.analysis.plot import (
    plot_kline, plot_backtest, plot_ic_heatmap,
    plot_quantile_nav, plot_yearly_returns,
)
plot_kline(df, name="600519", title="贵州茅台 收盘价与均线")     # 行情+均线
plot_backtest(result, name="600519")                            # 回测净值 vs 基准 + 回撤
plot_ic_heatmap(ic_table)                                       # 因子 IC 热力图(* = |NW t|>2)
plot_quantile_nav(nav, name="amihud_20")                        # 分层净值 Q1~Q5
plot_yearly_returns(df, name="600519")                          # 分年度收益(红涨绿跌)

# 便捷入口：demo_analysis.py 与 04_factor_research.py 已内置自动出图
```

回测：



```
from src.strategies.dual_ma import dual_ma_signal
from src.backtest.engine import run_backtest
from src.backtest.metrics import performance

sig = dual_ma_signal(df, fast=5, slow=20)
result, trades = run_backtest(sig, fee=0.00025, slippage=0.0005,
                              stamp_duty=0.0)      # 股票卖出印花税填 0.0005
stats = performance(result, trades)
```



***

## 七、技术指标清单

`analysis/indicators.py`（纯 pandas/numpy，无额外依赖）：

MA（5/10/20/60/120/250）、EMA、MACD（DIF/DEA/MACD 柱）、RSI（6/12/24）、

KDJ、布林带 BOLL、ATR、威廉 WR、CCI、OBV、动量 MOM、PSY；

`add_indicators(df)` 一次全部计算。



***

## 八、因子研究：什么因子才算「有效」

### 1. 因子是什么（和模型的区别）



* **因子（factor）= 一个可计算的「打分变量」**：每天给每只股票算一个数，刻画它的某个特征

  （动量、波动、换手、流动性……），它是**输入 / 特征**。

* **模型 = 把多个因子组合成一个最终预测**（如 LightGBM）。单因子是 "一个特征"，模型是 "特征的组合器"。

* 所以因子不是模型本身，而是模型的原料：先验证单因子有没有预测力，再谈建模。

### 2. 因子库（`analysis/factors.py`）



| 因子                | 含义                               |
| ----------------- | -------------------------------- |
| mom\_20 / mom\_60 | 过去 20 / 60 日收益率（动量）              |
| rev\_5            | 过去 5 日收益率（短期反转）                  |
| vol\_20           | 过去 20 日日收益标准差（低波因子）              |
| turnover\_20      | 过去 20 日平均换手率（投机度）                |
| rsi\_14           | 14 日 RSI（超买 / 超卖）                |
| ma60\_bias        | 收盘价相对 60 日均线的乖离度                 |
| amihud\_20        | Amihud 非流动性 \| 收益 \|/ 成交额（流动性溢价） |

因子在每个交易日做**横截面 rank（0\~1）**，方向不预设，由 IC 符号揭示。

### 3. 三板斧怎么看（`analysis/factor_test.py`）



1. **RankIC**：每天，因子 rank 与「未来 N 日收益 rank」的截面相关。

   经验门槛：|IC 均值 | > 0.03 有一定效果，> 0.05 较好；符号代表方向（负 = 因子值高 → 未来收益低）。

   统计输出含 tstat（普通 t 值）与 nw_tstat（Newey-West 自相关一致 t 值）：持有期>1 时 IC 日序列自相关，普通 t 值系统性虚高，显著性以 nw_tstat（|t|>2）为准。

2. **ICIR = IC 均值 / IC 标准差**：衡量稳定性，|ICIR| > 0.5 较稳。

3. **分层回测**：按因子分 5 组（Q1 最低 → Q5 最高），好因子各组收益应**单调排列**，并看 Q5−Q1 多空收益。

   **衰减**：对持有 1/5/10/20 日分别算 IC，看预测力几天消失。

### 4. 运行



```
# 抓沪深300成分日线 + 全部因子检验（可增量重复运行）
python scripts/04_factor_research.py
# 可选参数：指数代码、股票数量
python scripts/04_factor_research.py 000300 300
```

结果落 `data/store/factor_results/`：`ic_summary.parquet`（含 tstat 与 nw_tstat）、各组净值等。

### 5. 重要陷阱：为什么同时看「算术」和「中位数」

在沪深 300（**当前成分**）上，低波因子会出现看似矛盾的结果：



* **RankIC 为负（约 -0.022）**、多空**中位数**累计 **-93%** → 稳健口径：高波动股票 "典型" 未来收益更低，**低波异象方向成立**。

* 但多空**算术净值**一度 **+450%** → 这是**幻觉**，来自两点：

1. **肥尾**：高波动组约 8.5% 的日子单日暴涨 >5%（低波组仅 0.75%），少数极端暴涨拉高**算术均值**，但组内日收益**中位数为负**；

2. **幸存者 / 前视偏差**：用 "现在" 的成分回测 "过去"，高波动组富集了最终赌赢、靠暴涨跻身沪深 300 的公司，赌输被剔除的不在名单里。

**结论：判断因子有效性以 RankIC / ICIR / 中位数分层为准；算术累计净值对高波、正偏类因子会严重误导。**

要得到无偏分层，需用 "每个历史日期当时真实的指数成分（含后来退市 / 剔除的股票）"。

**本项已完成（point-in-time 成分已落地）**：`scripts/00_fetch_pit_constituents.py` 下载历史成分区间表（沪深300 自 2005-04-08、中证500 自 2007-01-15，来源 unliftedq/index-constitution，与中证官网当前成分交叉验证 300/300 一致），`build_factor_panel(pit_constituents=...) ` 按「当时真实成分」过滤面板，04 脚本默认启用 PIT 模式并对退市股用 baostock 补齐行情。

### 6. 本次实测结论（2018-01 \~ 2026-10，沪深 300，58.9 万行）



* 各因子 |RankIC| 多在 0.01\~0.04、|ICIR| 多在 0.1 上下 → **单因子预测力普遍偏弱**（真实且正常），

  需多因子合成 / ML 模型综合，这正是后续建模的动机。

* 方向上：A 股 20/60 日以**反转**为主（追涨杀跌不占优）；**低波、低换手、低乖离、超买回落**为负向有效；

  **Amihud 流动性溢价**为正、且随持有周期增强（20 日 IC≈0.042，长周期相对最强）。



***

## 九、已知边界与后续方向



* baostock 0.9.4 无 CPI/PPI/GDP/PMI 接口：CPI 由 akshare 提供；PPI/GDP/PMI 可按需接其他源。

* 分钟线由数据源返回最近约 1–2 个月，非全历史；实时快照默认不落盘（`save=True` 可存）。

* 北向资金 2024 年后披露口径调整，接口保留历史数据。

* 北交所代码（43/83/87/88 老号段、92 新号段）统一映射 `bj.` 前缀；新浪日线后端对北交所支持有限，失败会记录在批量更新报告里。

* 复权口径隔离约定：行情文件按 `{symbol}__{adjust}` 命名。若旧仓库存在无口径的 `{symbol}.parquet` 文件，请自行决定归属或重抓。

* 回测为**向量化、单标的、0/1 仓位**模型，适合择时验证；未做逐笔撮合与多标的组合。

* 因子分层已支持 **point-in-time 历史成分**（04 脚本默认启用，退市股用 baostock 补齐），幸存者偏差已消除。

  PIT 成分表（unliftedq/index-constitution，声明源自中证指数公司官方公告）已做如下核验：

  ① 当前截面与中证官网官方成分 **300/300 双向零差异**；② **2023-12-08** 调整与证券时报刊载的官方名单**逐只双向零差异**（调入 14/调出 14）；③ **2024-12-13** 调整，新闻点名的 12 只调入、9 只调出全部吻合；④ **2024-06-14** 调整点名个股全部吻合；⑤ 多段进出区间（如以岭药业）与公开记录自洽。

  已知口径：个别调整生效日在数据源中记在非交易日（边界日 ±1 只），不影响研究结论；若将来用于资金级决策，需对全部 71 个调整时点做全量核验。

后续可扩展：① 多标的组合 / 截面选股回测；② 事件驱动逐笔引擎；

③ ~~因子库与 IC / 分层检验~~（已完成，见第八节）；④ 定时任务每日自动更新；

⑤ 策略参数寻优；⑥ 多因子合成 + LightGBM 建模。

## 免责声明

本项目仅供学习与研究使用，不构成任何投资建议。

作者不对使用本工具产生的任何盈亏负责。