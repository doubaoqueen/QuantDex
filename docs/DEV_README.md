# QuantDex 开发者参考（DEV_README）

> 面向：维护者 / 想深入数据口径与研究结论的量化开发者。
> 2026-11-13 由原 README（425 行）拆分而来：README.md 只保留陌生人视角的定位/截图/快速上手，
> 维护者内容（目录结构 / Schema / API / 因子研究 / 已知边界）全部迁移到本文，内容未删减。
> 门外汉视角的操作手册见 `docs/PROJECT_GUIDE.md`。

---

## 一、目录结构

```
QuantDex/
├── config.py                     # 全局配置：数据目录、成本参数（QUANTDEX_STORE_DIR 可换仓，D-082）
├── requirements.txt
├── demo/store/                   # demo 数据包（~0.24MB，随仓库提交，D-082）
├── data/
│   └── store/                    # Parquet 数据仓库（不入 git，D-035）
│       ├── market/
│       │   ├── daily/{symbol}__{adjust}.parquet    # adjust: qfq/hfq/none（复权口径分文件隔离）
│       │   ├── daily_by_date/                      # 按日分区（append-only）
│       │   ├── weekly/  monthly/
│       │   └── minute/{symbol}__{freq}__{adjust}.parquet
│       ├── meta/                 # 列表、日历、PIT 成分、行业等
│       ├── valuation/ financials/ moneyflow/ macro/
│       ├── factor_results/ portfolio_results/ walkforward_results/
│       ├── reports/{large,small,div,topk}/         # 影子盘日报（Markdown）
│       ├── sim/                  # 影子盘 SQLite 账本（不入 git）
│       └── registry.parquet      # 数据注册表
├── src/
│   ├── data/                     # storage 仓库 / sources 数据源单例与重试 / schema 代码与复权 /
│   │                             #   universe 股票池与 PIT / market 行情 / snapshot 收盘快照 /
│   │                             #   financials / valuation / moneyflow / macro / partitions 按日分区
│   ├── analysis/                 # indicators 指标 / factors 因子库 / factor_test 检验 /
│   │                             #   expr 表达式工厂 / quality 质检 / report / plot（Pillow）
│   ├── strategies/               # dual_ma 双均线 / multi_factor 多因子（现行版、红利版、topk）
│   ├── backtest/                 # engine 单标的 / portfolio 组合 / metrics
│   ├── risk/                     # 单票上限 / 总仓 / 回撤断路器
│   └── live/                     # ledger 账本 / pipeline 流水线 / notifier 邮件 / open_exec 开盘撮合
├── frontend/                     # Vue3 控制台（dist 构建产物入库，node_modules 不入）
├── scripts/                      # 一键脚本（编号顺序见 scripts/README.md）
│   ├── 00~14                     # 数据抓取 / 研究 / 组合回测 / walk-forward / 影子盘 / 调度
│   ├── 16_webapp.py              # 本地控制台（零依赖 HTTP 服务 + 静态托管 frontend/dist）
│   ├── 17~43                     # 存储迁移 / 缓存重建 / 数字审计 / 调度器 / 分钟线回填等
│   └── run_daily.bat / run_webapp.bat / _bootstrap.bat   # 任务计划入口（纯 ASCII，见 scripts/README.md）
├── tests/                        # 零依赖回归测试（run_tests.py，80+ 项）
└── docs/                         # 决策日志 / 本参考 / 所有者手册 / 数据路线图 / blog
```

> 各目录均有独立 README.md：`src/`、`src/data/`、`src/analysis/`、`src/backtest/`、`src/strategies/`、`scripts/`、`data/`、`tests/`。

---

## 二、数据 Schema（标准化后）

全系统内部统一使用 **6 位纯代码**（如 `600519` / `510300` / `000300`）；
调用 baostock 时由 `schema.to_bs_code()` 自动加 `sh.` / `sz.` / `bj.`（北交所）前缀。

**日 / 周 / 月线**（`market/daily/{symbol}__{adjust}.parquet`，adjust 为 qfq/hfq/none）：

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
| source | 行级数据源（akshare/em、akshare/sina、baostock） |

> 复权口径（qfq/hfq/none）记在文件名与注册表 adjust 列：同标的、不同口径的数据落在不同文件，禁止混写。source 列保留每条记录出处，跨源 upsert 时可审计。get_kline 用 adjust 参数指定口径（默认 qfq，指数用 none）。

**分钟线**（`market/minute/{symbol}__{freq}__{adjust}.parquet`）：把 `date` 换成 `datetime`，其余相同。

**财务（baostock 季频）**：`financials/baostock/{symbol}__{类型}.parquet`，类型为
`profit`（盈利：ROE / 净利率 / 毛利率 / EPS / 总股本）、`operation`（营运）、`growth`（成长）、
`balance`（偿债 / 资产负债，含净资产）、`cashflow`（现金流）、`dupont`（杜邦）、
`dividend`（分红）、`adjfactor`（复权因子）、`express`（快报）、`forecast`（预告）。

财务表已标准化：统一携带 `symbol`（6 位）与 `bs_code`（sh./sz. 前缀），披露日统一为 `pub_date`、报告期统一为 `report_date`。做基本面因子时必须按 `pub_date`（当时已披露）对齐，而非 `report_date`，否则引入前视偏差。

**金额单位注意**（D-062 实测纠正）：北向 net_buy/买卖额为 **亿元**，但同表
cum_net_buy 为 **万亿元**、holdings_mv 为 **元**；两融各列实为 **元**（旧注释"万元"差 1e4 倍）。

---

## 三、作为库调用（API 速查）

```python
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

# 可视化（PNG 静态图，默认存 data/store/plots/，可用 out_dir 覆盖，返回文件路径）
from src.analysis.plot import (
    plot_kline, plot_backtest, plot_ic_heatmap,
    plot_quantile_nav, plot_yearly_returns,
)
plot_kline(df, name="600519", title="贵州茅台 收盘价与均线")     # 行情+均线
plot_backtest(result, name="600519")                            # 回测净值 vs 基准 + 回撤
plot_ic_heatmap(ic_table)                                       # 因子 IC 热力图(* = |NW t|>2)
plot_quantile_nav(nav, name="amihud_20")                        # 分层净值 Q1~Q5
plot_yearly_returns(df, name="600519")                          # 分年度收益(红涨绿跌)
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

## 四、技术指标清单

`analysis/indicators.py`（纯 pandas/numpy，无额外依赖）：
MA（5/10/20/60/120/250）、EMA、MACD（DIF/DEA/MACD 柱）、RSI（6/12/24）、
KDJ、布林带 BOLL、ATR、威廉 WR、CCI、OBV、动量 MOM、PSY；
`add_indicators(df)` 一次全部计算。

---

## 五、因子研究：什么因子才算「有效」

### 1. 因子是什么（和模型的区别）

* **因子（factor）= 一个可计算的「打分变量」**：每天给每只股票算一个数，刻画它的某个特征
  （动量、波动、换手、流动性……），它是**输入 / 特征**。
* **模型 = 把多个因子组合成一个最终预测**（如 LightGBM）。单因子是 "一个特征"，模型是 "特征的组合器"。
* 所以因子不是模型本身，而是模型的原料：先验证单因子有没有预测力，再谈建模。

### 2. 因子库（`analysis/factors.py`）

| 因子 | 含义 |
| --- | --- |
| mom_20 / mom_60 | 过去 20 / 60 日收益率（动量） |
| rev_5 | 过去 5 日收益率（短期反转） |
| vol_20 | 过去 20 日日收益标准差（低波因子） |
| turnover_20 | 过去 20 日平均换手率（投机度） |
| rsi_14 | 14 日 RSI（超买 / 超卖） |
| ma60_bias | 收盘价相对 60 日均线的乖离度 |
| amihud_20 | Amihud 非流动性 \| 收益 \|/ 成交额（流动性溢价） |

因子在每个交易日做**横截面 rank（0~1）**，方向不预设，由 IC 符号揭示。

### 3. 三板斧怎么看（`analysis/factor_test.py`）

1. **RankIC**：每天，因子 rank 与「未来 N 日收益 rank」的截面相关。
   经验门槛：|IC 均值| > 0.03 有一定效果，> 0.05 较好；符号代表方向（负 = 因子值高 → 未来收益低）。
   统计输出含 tstat（普通 t 值）与 nw_tstat（Newey-West 自相关一致 t 值）：持有期>1 时 IC 日序列自相关，普通 t 值系统性虚高，显著性以 nw_tstat（|t|>2）为准。
2. **ICIR = IC 均值 / IC 标准差**：衡量稳定性，|ICIR| > 0.5 较稳。
3. **分层回测**：按因子分 5 组（Q1 最低 → Q5 最高），好因子各组收益应**单调排列**，并看 Q5−Q1 多空收益。
   **衰减**：对持有 1/5/10/20 日分别算 IC，看预测力几天消失。

### 4. 运行

```bash
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
**本项已完成（point-in-time 成分已落地）**：`scripts/00_fetch_pit_constituents.py` 下载历史成分区间表（沪深300 自 2005-04-08、中证500 自 2007-01-15，来源 unliftedq/index-constitution，与中证官网当前成分交叉验证 300/300 一致），`build_factor_panel(pit_constituents=...)` 按「当时真实成分」过滤面板，04 脚本默认启用 PIT 模式并对退市股用 baostock 补齐行情。

### 6. 实测结论（PIT 修正后，2018-01 ~ 2026-10，沪深 300，63.7 万行）

**2026-10-19 更新：PIT 历史成分 + 退市股补齐后全量重跑，结论相对旧版（当前成分口径）发生了实质性变化——这正是幸存者偏差真实存在的直接证据。**

> ⚠️ 下表为**历史口径**（收益窗口含隔夜、按行 shift），保留作存档；**当前可成交口径（D-038）**的实时 IC 榜见量化学院（:8765）与 `factor_results/expr_ic_summary.parquet`——修正后 13/46 显著、核心排名未变。

| 因子 | RankIC(1日) | NW t(1日) | RankIC(20日) | NW t(20日) | 方向结论 |
|---|---|---|---|---|---|
| **turnover_20** | **-0.034** | **-4.61** | **-0.060** | **-2.52** | 低换手显著更强；分层**严格单调**（Qlow +50.5% vs Qhigh -35.9%） |
| **vol_20** | **-0.033** | **-4.47** | **-0.050** | **-2.10** | 低波显著更强，长周期仍显著 |
| rev_5 | -0.022 | -3.58 | -0.006 | -0.32 | 1 日反转显著，快速衰减 |
| mom_20 | -0.014 | -2.08 | -0.023 | -1.28 | 动量方向为负（追涨不占优） |
| ma60_bias | -0.011 | -1.45 | -0.026 | -1.31 | 乖离过大回归，中等强度 |
| amihud_20 | -0.005 | -1.21 | -0.003 | -0.23 | **旧版的"流动性溢价"（+0.042）在 PIT 修正后基本消失** |
| rsi_14 | -0.011 | -1.68 | -0.010 | -0.56 | 弱，方向不稳 |
| mom_60 | -0.002 | -0.33 | -0.015 | -0.76 | 弱 |

**要点**：
1. **低换手 + 低波** 是 PIT 口径下最强、最稳定的两个因子（NW t 约 -4.5，长周期衰减最小），且低换手分层呈**严格单调**，适合作为多因子组合的核心；
2. **旧版 Amihud 流动性溢价系数大概率是幸存者偏差的产物**——这是本项目"数据修正改变研究结论"的第一个完整案例；
3. 单因子 IC 幅度仍普遍偏小（0.01~0.06），多因子合成 / 组合管理是下一步方向；
4. 显著性判断一律以 **nw_tstat**（|t|>2）为准，普通 t 值会高估。

---

## 六、已知边界与后续方向

* baostock 0.9.4 无 CPI/PPI/GDP/PMI 接口：CPI 由 akshare 提供；PPI/GDP/PMI 可按需接其他源。
* 分钟线两条路（D-060）：东财 `fetch_minute` 只有滚动约 1–2 个月；**baostock `fetch_minute_bs` 提供 5/15/30/60 分钟线自 2011 年至今全历史**（存不复权口径，避开 qfq 锚点问题），已入每日调度（02b/10_daily_run 尾部增量，`29_fetch_minute.py` 可全史回填）。1 分钟线无免费全历史源。实时快照默认不落盘（`save=True` 可存）。
* 北向资金 2024 年后披露口径调整，接口保留历史数据。
* 北交所代码（43/83/87/88 老号段、92 新号段）统一映射 `bj.` 前缀；新浪日线后端对北交所支持有限，失败会记录在批量更新报告里。
* 复权口径隔离约定：行情文件按 `{symbol}__{adjust}` 命名。若旧仓库存在无口径的 `{symbol}.parquet` 文件，请自行决定归属或重抓。
* 回测为**向量化、单标的、0/1 仓位**模型，适合择时验证；组合/截面回测见 scripts/05、06。
* 因子分层已支持 **point-in-time 历史成分**（04 脚本默认启用，退市股用 baostock 补齐），幸存者偏差已消除。

  PIT 成分表（unliftedq/index-constitution，声明源自中证指数公司官方公告）已做如下核验：
  ① 当前截面与中证官网官方成分 **300/300 双向零差异**；② **2023-12-08** 调整与证券时报刊载的官方名单**逐只双向零差异**（调入 14/调出 14）；③ **2024-12-13** 调整，新闻点名的 12 只调入、9 只调出全部吻合；④ **2024-06-14** 调整点名个股全部吻合；⑤ 多段进出区间（如以岭药业）与公开记录自洽。
  已知口径：个别调整生效日在数据源中记在非交易日（边界日 ±1 只），不影响研究结论；若将来用于资金级决策，需对全部 71 个调整时点做全量核验。

后续可扩展：① 事件驱动逐笔引擎；② 定时任务每日自动更新 + **微信推送**（已定方向：**纯文本日报**，消息自包含当日数据——持仓/净值/信号/成交/结论，Markdown 表格形式，手机端直接看完，不依赖打开电脑；图片暂不传，如需再配图床）；③ 策略参数寻优；④ 多因子合成 + LightGBM 建模。
