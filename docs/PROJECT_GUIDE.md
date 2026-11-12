# QuantDex 功能与脚本说明书

> 面向：项目所有者（金融刚入门、以学习为目的）
> 更新：2026-11-13（补第 11~13 节：概念词典 / 操作 SOP / 排错升级）｜当前提交：见 `git log`
> 配套：各目录 README、`TODO.md`（待办）、`docs/DECISIONS.md`（决策日志）

---

## 0. 一页速览

**是什么**：A 股个人量化研究 + 影子盘系统。数据（PIT 历史成分、复权隔离、退市股补齐）→ 因子研究 → 组合回测 → walk-forward → 双影子账户（100 万观察 / 2000 元实盘模拟）→ 每日自动记账 + 邮件日报。

**现在能做什么**（已完成）：
- 抓取/更新 A 股行情、财务、估值、资金流、宏观、分红；PIT 历史成分（沪深300/中证500）
- 8 个量价因子检验（IC、NW t、衰减、5 分层、双口径中位数/均值）
- 组合回测（T+1、成本+印花税+最低佣金+过户费、涨跌停/停牌跳过）
- walk-forward 防过拟合（2022–2026 样本外）
- 市值中性化检验（因子是否只是小盘代理）
- 双影子账户：SQLite 账本（快照/持仓/成交）、T+1 开盘撮合、日报、邮件推送
- 每日自动运行（Windows 任务计划 19:00；另有 APScheduler 常驻版可选）
- 80+ 项回归测试、决策日志、数据注册表

**还不能做**：行业中性化、未复权+现金分红口径、事件驱动回测、实盘下单、盘中/实时。

---

## 1. 环境与快速开始

```bash
# 环境（已配好）
D:\conda\miniconda3\envs\QuantDex\python.exe   # Python 3.11 + pandas/numpy/pyarrow/akshare/baostock

# 首次完整搭建（约 20-40 分钟，视网络）
python scripts/00_fetch_pit_constituents.py   # PIT 历史成分（沪深300/中证500）
python scripts/00_fetch_calendar.py           # 交易日历
python scripts/01_init_universe.py            # 股票/ETF/指数/可转债列表
python scripts/02_update_market.py            # 行情（观察池）
python scripts/04_factor_research.py          # PIT 全量行情 + 因子研究（抓+算，最耗时）

# 日常（已自动化，无需手动）
# 任务计划 19:00 → run_daily.bat → 02b 更新行情 → 10 两个账户 → 邮件
```

---

## 2. 目录结构

```
QuantDex/
├── src/
│   ├── data/        # 数据层：storage 仓库 / sources 数据源 / market 行情 /
│   │                #   schema 代码与复权 / universe 股票池与PIT / financials 财务分红 /
│   │                #   valuation 估值 / moneyflow 资金流 / macro 宏观 / quality 质检
│   ├── analysis/    # 指标/因子(手写+表达式工厂)/因子检验/实验存档/报告/绘图
│   ├── strategies/  # 双均线 / 多因子(动量+低波、低换手+低波+反转)
│   ├── backtest/    # engine 单标的 / portfolio 组合 / metrics
│   ├── risk/        # 单票上限/总仓/回撤断路器
│   └── live/        # ledger 账本 / pipeline 流水线 / notifier 邮件
├── scripts/         # 00~14 一键脚本（见第 4 节）
├── tests/           # 80+ 项回归测试（run_tests.py）
├── data/store/      # 数据仓库（926 日线 + 835 分红 + 财务/估值/资金流/宏观）
├── docs/            # DECISIONS.md 决策日志 / PROJECT_GUIDE.md 本文 / blog/
└── README.md
```

---

## 3. 功能模块 × 脚本对照（总表）

| 功能 | 脚本 | 输入 | 输出 | 前置 |
|---|---|---|---|---|
| PIT 历史成分 | `00_fetch_pit_constituents.py` | 网络 | `meta/pit_constituents_000300/000905` | 无 |
| 交易日历 | `00_fetch_calendar.py` | 网络 | `meta/trade_calendar` | 无 |
| 股票池 | `01_init_universe.py` | 网络 | `meta/stock_list` 等 | 无 |
| 行情更新（观察池） | `02_update_market.py` | 网络 | `market/daily/*` | 01 |
| **行情更新（PIT 全量）** | `02b_update_pit.py` | 网络 | `market/daily/*` | 00 |
| 财务/估值/资金流/宏观 | `03_fetch_alternative.py` | 网络 | `financials/`、`valuation/` 等 | 01 |
| **因子抓取 + 检验** | `04_factor_research.py` | 网络+本地 | `factor_results/` | 00/01 |
| **组合回测** | `05_portfolio_backtest.py`（第4参可传 `dividend`） | 本地 | `portfolio_results/`（div 版带 `_div` 后缀） | 04 |
| **walk-forward** | `06_walkforward.py`（第2参可传 `dividend`） | 本地 | `walkforward_results/` | 04 |
| 账本演示 | `07_ledger_demo.py` | 本地 | 临时账本（不碰生产） | 无 |
| **影子盘流水线** | `08_daily_pipeline.py` | 本地 | 账本 + `reports/` + 邮件 | 02b/04 |
| 邮件连通测试 | `09_test_mail.py` | 网络 | 测试邮件 | `.env` |
| **每日一键** | `10_daily_run.py` | 网络+本地 | 双账户 + 日报 + 邮件 | 02b（或内置更新） |
| **市值中性化** | `11_neutralize_factors.py` | 本地 | `neutralization_summary.parquet` | 04 面板数据 |
| 分红抓取 | `12_fetch_dividends.py` | 网络 | `financials/baostock/*__dividend` | 00 |
| 定时任务安装 | `13_setup_task.ps1` | 无 | Windows 任务计划 | 无 |
| **Python 调度器** | `14_scheduler.py` | 无 | 常驻调度（APScheduler） | `pip install apscheduler` |
| 数据源诊断 | `net_diag.py` / `net_diag_github.py` | 网络 | `*_result.txt` | 无 |
| **本地控制台（Vue3）** | `16_webapp.py` + `frontend/` | 本地 | http://127.0.0.1:8765 | 无 |
| **P0 参考数据** | `20_fetch_reference_data.py` | 网络 | 全量证券/申万行业/复权因子 | 00 |
| **市场状态过滤器回测** | `21_regime_filter_backtest.py` | 本地 | `portfolio_results/regime_filter_*` | 04 |
| **表达式因子工厂检验** | `22_factor_factory.py` | 本地 | `factor_results/expr_ic_summary.parquet` | 00 |
| 单标的演示 | `demo_analysis.py` | 本地 | 图 + 回测 | 02 |
| 测试 | `tests/run_tests.py` | 无 | 80+ passed | 无 |

---

## 4. 每个脚本详解

### 数据层
- **00_fetch_pit_constituents.py**：从开源数据集（unliftedq/index-constitution，源自中证公告）生成"纳入/剔除区间"；已做三点外部核验（当前 300/300、2023-12 全量名单、2024-06/12 点名）。
- **00_fetch_calendar.py**：下载交易日历（1990–今），并检查行情覆盖率。
- **01_init_universe.py**：股票/ETF/指数/可转债列表、全量证券（含 IPO/退市）、行业分类（baostock；本机全量接口偶发挂起）。
- **02_update_market.py**：更新观察池（18 只示例）行情。
- **02b_update_pit.py**：**每日任务第一步**。更新 949 只 PIT 历史成员行情（增量）。默认东财、禁用新浪降级（新浪的 py_mini_racer 在本机会段错误）。可 `python scripts/02b_update_pit.py 5` 只跑前 5 只。
- **03_fetch_alternative.py**：估值（百度）、财务摘要、六大季频、快报预告、分红、复权因子、资金流、龙虎榜、宏观。**注意：目前只抓 2 只示例，需扩展为按 PIT 并集批量。**
- **04_factor_research.py**：PIT 全量日线（退市股 baostock 补齐）→ 因子面板（PIT 过滤）→ IC/ICIR/NW-t/衰减/分层 → 图。**研究的主入口**。
- **12_fetch_dividends.py**：分红增量抓取（835/949 已完成），可断点续跑。

### 研究层
- **05_portfolio_backtest.py**：组合/截面回测。命令 `python scripts/05_portfolio_backtest.py [指数] [TopN] [调仓日] [corrected|legacy]`。当前结果（开盘执行口径 D-034）：+39.81%（2018–2026）、MDD -29.26%。
- **06_walkforward.py**：逐年训练选参 → 样本外。开盘口径（D-034）：OOS +32.30%、年化 6.21%、IR 0.71、MDD -14.48%。
- **11_neutralize_factors.py**：市值中性化（市值代理 = 成交额/(换手率/100)）。结论：turnover/vol 中性化后仍显著；amihud 翻正。
- **demo_analysis.py**：单标的质检→概览→分年度→双均线回测→出图。

### 模拟盘
- **07_ledger_demo.py**：账本演示（临时库，绝不碰生产）。
- **08_daily_pipeline.py**：影子盘流水线。`--capital/--db/--label/--max-positions/--dry-run/--no-push`。
- **10_daily_run.py**：每日一键（更新→大账户→小账户→邮件）；`--skip-update` 只跑账户。
- **09_test_mail.py**：邮件配置与连通性测试（`.env`：SMTP_HOST/PORT/USER/PASS/REPORT_TO，已 gitignore）。

### 调度
- **13_setup_task.ps1**：Windows 任务计划安装/卸载（每天 19:00，错过补跑）。**任务名 `QuantDex Daily`，已安装（2026-10-21），下次 10-22 19:00。**
- **run_daily.bat**：任务实际动作：`02b_update_pit.py` → `10_daily_run.py --skip-update`，日志 `data/store/logs/daily_run.log`。
- **14_scheduler.py**：Python 原生调度（APScheduler），`--run-now` 立即执行；需常驻 + 开机自启（与任务计划二选一，不要同时跑）。

---

## 5. 数据仓库说明（`data/store/`）

| 路径 | 内容 | 规模 |
|---|---|---|
| `market/daily/{code}__{qfq,hfq,none}.parquet` | 日线，复权口径隔离 | 926 文件 |
| `market/weekly|monthly|minute/` | 接口具备，未纳入流水线 | 空 |
| `financials/baostock/*__dividend.parquet` | 分红（除权日/每股税前红利） | 835 |
| `financials/baostock/*__balance/income/...` | 财务六件套 | 少量示例 |
| `meta/` | 股票池、交易日历、PIT 成分、全量证券(含退市)、申万行业 | 7 |
| `valuation/ moneyflow/ macro/` | 估值、资金流、宏观 | 少量 |
| `registry.parquet` | 数据集注册表（名称/行数/日期/来源） | 1800+ 数据集 |
| `factor_results/` | IC 汇总、分层净值、size_panel、中性化结果 | — |
| `portfolio_results/` | 组合净值/权重/图 | — |
| `walkforward_results/` | 折明细、样本外净值 | — |
| `reports/large|small/` | 影子盘日报（Markdown） | — |
| `sim/ledger.sqlite` | 大账户账本（SQLite WAL） | — |
| `sim/ledger_2000.sqlite` | **2000 元账户账本** | — |
| `sim/ledger_div.sqlite` | **红利账户账本**（低换手+低波+股息率，D-027） | — |
| `logs/` | daily_run.log / scheduler.log | — |

**口径铁律**：复权文件禁止混写；qfq 收益已含分红（不要再叠加，见 DECISIONS D-007）。

---

## 6. 三条使用路线

1. **研究复现**：00 → 01 → 04 → 05/06 → 看图（`data/store/*/plots`）
2. **每日自动化**：任务计划 19:00 → 02b → 10 → 邮件（`data/store/logs/daily_run.log`）
3. **临时研究**：`python scripts/04_factor_research.py` 后直接跑 05/06/11

---

## 7. 当前状态与关键数字

- 组合回测（2018-01~2026-10，Top50/20日，保真+**开盘执行**口径 D-034）：**+39.81%**，MDD -29.26%
- walk-forward（2022–2026 OOS，开盘口径 D-034）：**+32.30%**，年化 6.21%，IR 0.71，MDD -14.48%
- 市值中性化：turnover_20 IC -0.0499（t -2.23）、vol_20 -0.0466（t -2.06）→ 不是纯小盘代理
- 影子盘：large 49 持仓；small 3 持仓（2000 元）；**div 红利账户 49 挂单（2026-10-24 起，D-027）**——开盘口径（D-034）：红利 WF 样本外 +55.27%/IR 0.91，topk +76.24%/IR 1.10，现行 +32.30%/IR 0.71，并行前瞻验证
- 行业中性化（D-021）：低换手/低波行业内仍显著（turnover IC -0.032/t -2.47），约一半 IC 来自行业间差异
- 市场状态过滤器（D-022）：R1/R2 回测均大幅吃收益且不改善 MDD——**不启用**
- 测试：**80+ passed**
- 决策日志：D-001 ~ D-008（T+1、费用、保真回测、WF、中性化、双账户、分红口径、调度崩溃）

---

## 8. 已知限制 / 实盘前待办

| 项 | 状态 |
|---|---|
| 行业中性化 | ⏳ 待做（baostock 行业接口挂起，需换源） |
| 未复权价格 + 现金分红口径 | ⏳ 需先抓未复权日线 |
| 冲击成本、成交量约束 | ⏳ 未建模 |
| 事件驱动回测（PEAD/公告） | ⏳ 未做 |
| 影子盘实跑 ≥4 周 + 偏差归因 | ⏳ 进行中（10-21 起） |
| 实盘通道/风控规则书面化 | ⏳ 最后一步 |
| 数据出 git（LFS/Release） | ⏳ 仓库 120MB+ |

---

## 9. 排错手册

| 现象 | 原因 | 处理 |
|---|---|---|
| 定时任务崩溃、日志有 py_mini_racer | 新浪 JS 引擎段错误 | 已修复：02b 默认东财、禁用新浪降级；更新/流水线解耦 |
| `schtasks` 报"系统找不到指定的文件" | 任务未创建 | 运行 `13_setup_task.ps1` |
| `/Query` 看不到任务 | 同上 | 同上 |
| 邮件没收到 | SMTP 未配置/发件被拦 | 跑 `09_test_mail.py` 看输出；检查垃圾箱 |
| 抓数失败/超时 | 东财限流、代理、网络 | 跑 `net_diag.py`；降 workers；稍后重试 |
| 图表打不开/崩溃 | 旧 matplotlib 崩溃史 | 已改 Pillow 绘图 |
| 编码乱码 | PowerShell 重定向是 UTF-16 | 用 `Get-Content -Encoding Unicode` 或直接用 Python 读 |
| 回测结果与 walk-forward 差异大 | 全周期 vs 样本外、参数不同 | 正常；看 DECISIONS D-004 |

---

## 10. 关键文件索引

- 待办：`TODO.md`
- 决策日志：`docs/DECISIONS.md`
- 数据路线图（数据现状盘点 + 补数计划）：`docs/DATA_ROADMAP.md`
- 各目录说明：`src/data/README.md`、`src/analysis/README.md`、`src/backtest/README.md`、`src/strategies/README.md`、`src/live/README.md`、`scripts/README.md`、`data/README.md`、`tests/README.md`
- 博客：`docs/blog/第二篇.md`

---

## 11. 概念小词典（门外汉版）

每个词一句话 + 在本项目里对应什么：

| 术语 | 大白话 | 本项目对应 |
|---|---|---|
| **净值（NAV）** | 账户总市值 ÷ 期初本金，1.0 起步；1.05 = 赚了 5% | 控制台四张账户卡的「总净值」 |
| **回撤（MDD）** | 从历史最高点跌下来最多多少 %，衡量"最惨时有多惨" | 日报/回测里的「最大回撤」 |
| **年化收益** | 把任意长度的收益折算成"一年赚多少"，便于不同期限比较 | 回测面板「年化」 |
| **夏普 / IR** | 每承受 1 份波动换来多少收益，越高越"稳赚" | 回测绩效「夏普比率 / 信息比率」 |
| **前复权（qfq）** | 把历史价格按分红送股调整到"今天可比"，否则除权日会有假跳空 | 行情文件名 `__qfq`；收益计算默认用它 |
| **T+1** | 今天买明天才能卖（A 股规则）；本项目撮合按「今日收盘挂单 → 明日开盘价成交」 | 订单面板「人工挂单（T+1 开盘撮合）」 |
| **涨跌停** | 主板 ±10%、创业板/科创板 ±20%、ST ±5%；到价买不进/卖不出 | 撮合拒单 + 「涨跌停观察」面板 |
| **因子** | 每天给每只股票打的一个分（如换手率越低分越高），用于排序选股 | 「因子」面板的三列分数 |
| **IC / NW-t** | 打分与未来收益的相关性；NW-t 是更严格的显著性检验，\|t\|>2 才算数 | 因子研究输出（DECISIONS D-042） |
| **分层回测** | 按分数把股票分 5 组，看各组收益是否"分越高赚越多"（单调） | 04 脚本输出 |
| **walk-forward** | 每年只用"以前的数据"重新选参数，防止"对着答案调参" | 06 脚本；控制台「回测」面板蓝线 |
| **PIT / 幸存者偏差** | 用"当时真实的指数成分"回测，否则只有活下来的好公司入选，结果虚高 | 00 脚本 + D-042 的 FDR 检验 |
| **影子盘** | 假钱真记账：挂单、撮合、日报全按真实规则走，一分钱不投 | 四个账户 = 4 条影子盘 |
| **调仓节奏 20 日** | 策略每 20 个交易日按纪律换一次仓，平时不动作 | 「策略动向」面板进度条 |
| **红利版 / topk / 现行版** | 同一套框架跑的三种策略配方，影子盘并行验证谁更稳 | 账户下拉里的分组 |

---

## 12. 每日 / 每周操作 SOP

**每天（约 1 分钟，全靠自动化）**：
1. 看 19:00 后到的**邮件日报**（四个账户合并一封）。没到 → 看第 13 节排错。
2. 有空就开控制台（开机自启）：看账户卡（今天赚亏）、涨跌停观察、策略动向进度条。
3. 想手动干预：订单面板「人工挂单」输入代码/数量 → 次日开盘按现价撮合。

**每周（5 分钟）**：
- 打开 `data/store/logs/` 扫一眼 `webapp_run.log` 尾部有没有异常（`Traceback`、`失败 N`）。
- 控制台「后台任务」点一次「更新行情」，看快照是否秒级完成（若转 baostock 长跑说明东财又抖了）。

**每月**：
- `python scripts/23_backup_data.py` 备份数据仓库（本地保管）。
- `python scripts/19_audit_numbers.py` 数字审计（净值恒等式/现金连续性，全 PASS 才安心）。
- `python tests/run_tests.py` 回归全绿。

---

## 13. 排错手册（升级版，含 2026-11 真实案例）

| 现象 | 原因 | 处理 |
|---|---|---|
| **邮件没收到 / 深夜才到** | ① webapp 没在跑（开机任务失败）；② 东财快照瞬时断连 → 回退 baostock 逐只补齐（要跑几小时）；③ 在垃圾箱 | 先查垃圾箱；再看 `webapp_run.log` 找 `[notifier] 邮件已发送`（发了就是到达问题）；`[spot] 东财快照源不可用` 出现说明走了慢路——2026-11-13 起快照已加 45s×3 次重试，仍慢才兜底 |
| **等数据十几二十分钟** | 东财失败转 baostock 全量兜底 | 现在先重试 3 次（45s 间隔）；若日志显示 `进度 X/949` 在慢慢爬，就是兜底在跑，让它跑完别中断 |
| **控制台打不开 / 端口 8765 无响应** | ① 已有一个实例在跑（webapp.lock）；② 端口被占 | 看 `data/store/logs/webapp.log`：`[启动失败] 已经有一个控制台在跑` → 找到旧窗口关掉；`Get-Process python` 确认 |
| **开机自启没生效** | 任务计划 "QuantDex Web"/"QuantDex Daily" 的 bat 出问题 | bat 已全部 ASCII 化（UTF-8 中文注释在 cp936 控制台会非确定性解析错位——11-12 静默失败的根因，勿再加中文注释回 .bat） |
| **换电脑/换人 clone 后启动脚本找不到 Python** | 解释器路径因人而异 | `_bootstrap.bat` 自动探测；探测不到设环境变量 `QUANTDEX_PYTHON` 或建 `scripts/python_path.local.bat`（不入库） |
| 数据过期（控制台红色提示） | 几天没开机 | 开控制台会自动补；急就点「更新行情」 |
| 定时任务崩溃、日志有 py_mini_racer | 新浪 JS 引擎段错误 | 已修复：02b 默认东财、禁用新浪降级 |
| `schtasks` 报"系统找不到指定的文件" | 任务未创建 | 运行 `13_setup_task.ps1` |
| 邮件 SMTP 报错 | `.env` 配置/授权码 | 跑 `09_test_mail.py` 看输出 |
| 抓数失败/超时 | 东财限流、代理、网络 | 跑 `net_diag.py`；降 workers；稍后重试 |
| 图表打不开/崩溃 | 旧 matplotlib 崩溃史 | 已改 Pillow 绘图 |
| 编码乱码 | PowerShell 重定向是 UTF-16 | 用 `Get-Content -Encoding Unicode` 或直接用 Python 读 |
| 回测结果与 walk-forward 差异大 | 全周期 vs 样本外、参数不同 | 正常；看 DECISIONS D-004 |
