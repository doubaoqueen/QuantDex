# BigQuant 核心能力深挖：机制 → 对照 → 复刻路径

> 2026-11-14 第二次调研（第一次见 bigquant_survey.md——那是广度盘点，这次按能力拆机制）。
> 范围纪律：只对比**个人量化用得上的核心能力**，社区/服务/会员体系明确排除。
> 每项能力回答三问：**他们怎么实现的 → QuantDex 现状 → 复刻实质要做什么（值不值得）**。

---

## ① 数据层：底层数据表的字段覆盖 + DAI 统一接口

### 他们的机制

- **底层表**（比预计算宽表更重要——官方自己都不推荐用宽表 cn_stock_prefactors，建议从底层表取数自算）：行情 cn_stock_bar1d、估值 cn_stock_valuation、技术指标 cn_stock_factors_ta、资金流 cn_stock_moneyflow、财务三表（items/indicators/extend）、行业（申万 2014/2021 + 中信 1/2/3 级）、股本股东 cn_stock_shares。
- 字段覆盖实录（来自数据字典）：技术指标**多参数档位系统化命名**（cci_14/20/30/60/120、rsi_12/24/48/60/120、kdj_rsv_10_3_6/14_3_3/20_5_5…）；**资金流按单型拆分**（active_buy_amount_large/big/mid/small/all + 各占比 + 净流入）；财务因子全套（ROE/ROA/ROIC/EBITDA/毛利率/CAGR/连续 N 季最小值类）；**股东户数及其环比**（total_shareholder_chg_1q~4q）；**涨跌停价直接入库**（upper_limit/lower_limit）；标记位（is_kcb/is_hs300/is_zz500/is_risk_warning/suspended/margin_trading_status）。
- **DAI 统一接口**：SQL 引擎 + `m_` 前缀时序算子（按 instrument 分组）、`c_` 前缀截面算子、Python UDF 注册（dai.DaiUDF）、**pandas DataFrame 可绑定进 SQL 参与 JOIN**（bind_relations）、输出可转 df/polars/arrow。

### QuantDex 现状

ParquetStore + expr_factors 表达式工厂（47 因子）。逐项对：

| 字段族 | 他们 | 我们 | 判定 |
|---|---|---|---|
| 量价（含涨跌停价） | ✓ | ✓（涨跌停价用 board_limit 现算，口径集中反而更好） | 已对齐 |
| 技术指标多参数档 | 系统化命名 | indicators.py 有全套算法，无档位矩阵 | 小差距（需要时展开即可） |
| **个股资金流（单型拆分）** | ✓ 专表 | ✗（只有北向/两融，个股主力单型无） | **实质差距**——akshare 有东财个股资金流接口可补 |
| 财务因子全套 | ✓ 三表 | P1-b 待补（黑名单阻塞中） | 已排期 |
| 估值 | ✓ 专表 | P1-a 待补 | 已排期 |
| 股东户数及环比 | ✓ | ✗ | 可补（akshare 有接口），但优先级低 |
| 标记位（ST/成分/停牌） | ✓ | ✓（PIT 成分 + ST 名单 + 撮合拒单） | 已对齐 |
| 分红股息率 | ✓ 字段 | ✓（红利版 dy_ttm 自算） | 已对齐 |

### 复刻路径

不整批抄——按已排期走：P1-a/b（估值+财务）解锁最大两块；**新增一条 P1-c：个股资金流（东财单型拆分，akshare 免费）**，它是量价类因子里我们唯一缺的底层族。股东户数缓行。数据字典文档化我们已有（DATA_ROADMAP.md），补字段清单即可。

---

## ② 因子计算引擎：DAI SQL vs expr 工厂

### 他们的机制

DAI 本质 = **分布式 SQL 因子引擎**：因子写成 SQL 表达式（m_lag/c_rank 算子前缀 + 窗口函数 + UDF），引擎负责按 instrument 分组的时序计算和按日期的截面计算，GPU 加速，输出接 pandas/polars。

### QuantDex 现状

expr_factors.py：47 个因子用 Python 表达式（自研算子 Ref/Mean/Std/Corr/Skew/Greater/Delta…）在 pandas 面板上计算。

### 判定：**本质同构，不落后**

两者是同一思想（表达式声明因子，引擎代算）的两种实现。差距只在吞吐（他们 PB 级分布式，我们单机 949 只——够用）。**可借鉴一点**：DAI 的 UDF 注册机制——允许用户注册自定义 Python 函数进因子库。我们的 expr 工厂目前只收预定义算子组合，可以加一个"注册自定义函数"的入口，让新因子从"改库文件"变成"注册一行"。中优先级。

---

## ③ 模型层：StockRanker 流水线 + AI 因子商品化

### 他们的机制

- **StockRanker** = Learning-to-Rank + GBDT，全市场 ~3000 只一起学排序。官方五步流程：定义目标并标注（**标注 = 未来 N 天收益，自动算无需人工**）→ 训练/评估/测试集划分 → 特征 → `M.stock_ranker_train` → 回测。常见问题清单 themselves 写明：过拟合、数据集重叠、划分不当、训练集太少、预处理不足、标注与特征逻辑不一致、"因子并非越多越好"。
- **cn_stock_factors_ai**：把 10 个模型（CNN/DNN/ResNet/**LightGBM**/LSTM/RF/RNN/StockRanker/XGBoost/GRU）的日频预测值直接当因子数据卖。注意：**起始 2026-10-16——上线才一个月**，产品化尝试而非成熟资产。

### QuantDex 现状

模型层为零；TODO 路线 4 已排 LightGBM（闸门之后）。

### 复刻路径（这一项有直接可执行的发现）

**LightGBM 原生支持 lambdarank/rank_xendcg objective——开源版 StockRanker 无门槛**：特征 = 我们的因子面板（55 列），group = 每个交易日的股票集合，标签 = 未来 5 日收益分档。这就是"排序学习 + GBDT"的全部要件，和 StockRanker 同型。TODO 里的 LightGBM 项应明确用 **rank objective**（而非回归），一步对齐他们的招牌。其余 10 模型（LSTM/GRU/ResNet…）不必复刻——表格因子上树模型是性价比主力，深度模型是 attention 占位完成后的实验。

---

## ④ 高级优化四件套

### 他们的机制

AIStudio"高级优化"模块：**超参搜索**（画布内定义参数与搜索方式，自动扫描）、**滚动训练**（训练/测试集定期轮换的算子，官方示例"42 次训练预测"）、**组合优化器**（输入收益预期+风险预算+约束+风险模型 → 输出最优配置）、**归因分析**。

### QuantDex 现状与判定

| 件 | 他们 | 我们 | 判定 |
|---|---|---|---|
| 滚动训练 | 算子化 | 06_walkforward 就是滚动训练（每年重选参） | **已有，同型**——差距仅是"只选参数不选因子清单"（TODO 已记） |
| 超参搜索 | 自动搜索模块 | 06 手动参数网格 | 小差距——接 Optuna 即可自动化（低优先） |
| 组合优化器 | ✓ | ✗（等权 + 单票上限） | 远期——个人资金规模下等权已够，优化器收益边际小 |
| 归因分析 | ✓ 模块 | D-068 在建（执行层测量工具已就绪，11-20 四周归因） | **正在做**，且我们做的是更难的"现实 vs 回测"归因 |

---

## ⑤ 回测引擎：BigTrader

### 他们的机制

时间驱动 Bar 引擎，撮合价格可买/卖分开指定（如买开盘卖收盘）；滑点支持 VolumeShareSlippage（成交量占比 + 价格冲击系数，volume_limit=0.025 / price_impact=0.1）；C++ 核心（无性能数字公开）。

### 关键发现：他们的文档缺口

**涨跌停处理在文档中未说明**（第三方对比帖也指出"文档未说明引擎是否拒绝涨停开盘的买单，回测后需检查被拒单"）。我们这边的同项：board_limit 唯一来源 + 撮合拒单 + 幂等唯一索引 + 现金护栏，全部文档化且有测试。

### QuantDex 现状与复刻

- 判定：**口径纪律我们反超**；性能层面他们的 C++ 我们不需要（949 只日频向量化秒级）。
- 唯一值得抄的实质：**VolumeShareSlippage 式冲击成本模型**——对应 PROJECT_GUIDE §8 已知未建模项。路径：D-067 的滑点校准数据 → 拟合 price_impact 参数 → 撮合时按单量/该根 K 线成交量限速。中优先级，排归因之后。

---

## ⑥ LLM 工具层：Cowork / 大模型投研

### 他们的机制

Cowork = "AI 原生协同平台"（Skill 创建→调用，演示复现华泰金工研报做自动化因子投研）；BigAlpha 2026 有"端到端大模型"赛道，展示多智能体系统（事件感知→产业链推理→组合回测→研报生成）；有 AI 全景综述文档（FinBERT/FinLLaMA 等）。

### QuantDex 现状与复刻

零。但这是上一轮讨论"新闻流"的自然延伸，且个人可做**最小版本**：

- **研报复现助手**：LLM 批量读券商研报 → 结构化输出（因子假设/表达式/数据需求）→ 灌进 expr 工厂测试 → 走闸门。他们宽客学院拿这个当课程卖，我们用 API 一晚上能搭雏形
- 依赖前置：P1-b 财务数据到位后，可复现的研报类型才多
- 优先级：中——排在模型层 LightGBM 之后，但比组合优化器靠前

---

## ⑦ 比赛数据栈（顺带一提）

BigAlpha 2026 数据栈设计：中证1000 成分池 + 1 分钟 K 线 + **盘口快照** + 统一因子库表（factorlib）+ 风险暴露表（exposure）。可借鉴的不是数据（拿不到），是**表设计**：factorlib（因子注册表）和 exposure（风险暴露）两张表的思想，与我们 registry.parquet + 未来中性化暴露表同构——确认我们的存储设计方向没有落后。

---

## 总结：真实的差异（剥掉服务外壳后）

**他们真正的核心能力只有两个，且都可复刻：**

1. **数据字段的完整度**（个股资金流单型拆分、财务因子三表、估值）——复刻路径 = 已排期的 P1-a/b + 新增 P1-c 个股资金流
2. **标注→训练→滚动→部署的 ML 流水线**——复刻路径 = LightGBM rank objective（对标 StockRanker）+ 06 已有的滚动框架，闸门后启动

**三个他们没有、我们有的：** 口径文档化（他们的涨跌停处理文档缺失 vs 我们全部口径有编号可查）、多重检验诚实（FDR 写在学习页）、影子盘真记账 + 事故复盘文化。这些不产生收益，但决定"你敢不敢把真钱交给这套系统"——正是 D-076 开户体检的门槛。

**四个同构/不必追：** 表达式引擎（同构）、滚动训练（同型）、社区/学院/比赛（服务型）、C++ 引擎（性能型，个人无此需求）。

## 行动清单（合并进 TODO 的建议）

1. TODO 数据补全新增 **P1-c：个股资金流（东财单型拆分，akshare）**——P1-b 之后的下一批
2. TODO 模型层 LightGBM 条目补充：**用 rank objective（lambdarank）直接对标 StockRanker**
3. （低优）expr 工厂加 UDF 注册入口；（低优）Optuna 接 06 的参数网格；（中优）VolumeShareSlippage 式冲击成本——排在 D-068 归因之后
4. 研报复现 LLM 助手——模型层跑通后的下一个研究项目

## 来源（全部公开页面）

- [预计算因子表字典](https://bigquant.com/data/datasources/cn_stock_prefactors) · [AI 因子表（10 模型）](https://bigquant.com/data/datasources/cn_stock_factors_ai) · [BigAlpha/DAI 文档](https://bigquant.com/wiki/doc/A4OKulDOt3)
- [StockRanker 策略示例（历史文档）](https://bigquant.com/wiki/doc/qylSVzUeYZ) · [StockRanker 介绍](https://bigquant.com/wiki2/doc/8DaJZW49tZ)
- [高级优化介绍](https://bigquant.com/square/paper/110bef44-c9d4-464a-b47c-a153534dbf8b) · [滚动训练算子](https://bigquant.com/wiki2/doc/VpHnetW7CZ) · [组合优化器算子](https://bigquant.com/wiki2/doc/f2OeAPjy5t)
- [BigTrader 回测](https://bigquant.com/wiki2/doc/3gG2rg4jBd) · [撮合价格设置](https://bigquant.com/wiki2/doc/jiage-MpFKBg9fv8) · [滑点指南](https://bigquant.com/wiki/doc/VllMcX8bUq)
- [Cowork Skill 指南](https://bigquant.com/wiki/doc/PoEjAk1O2q) · [AI 全景综述](https://bigquant.com/wiki/doc/cxpcp1o1FG) · [BigAlpha 2026](https://bigquant.com/bigalpha/2026)
