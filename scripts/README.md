# scripts/ — 一键脚本

> 按编号顺序执行即可跑通整个项目。所有脚本需在**项目根目录**运行；
> 建议使用 conda 环境 `QuantDex`（Python 3.11）。

## 运行顺序

| 步骤 | 脚本 | 作用 | 可重复运行 |
|---|---|---|---|
| 0 | `00_fetch_pit_constituents.py` | 下载 PIT 历史指数成分表（消除幸存者偏差的地基数据） | ✅ 幂等 |
| 0b | `00_fetch_calendar.py` | 补全交易日历 `meta/trade_calendar`（增量跳过逻辑依赖它） | ✅ 幂等 |
| 1 | `01_init_universe.py` | 标的池初始化：股票/ETF/指数/可转债列表、交易日历、全量证券、行业、指数成分 | ✅ |
| 2 | `02_update_market.py` | 批量增量抓取核心标的日线（东财为主，失败自动降级新浪） | ✅ 增量 |
| 3 | `03_fetch_alternative.py` | 估值 / 财务 / 资金流 / 宏观 / 龙虎榜 | ✅ |
| 4 | `04_factor_research.py` | 因子研究：PIT 历史成分 → 抓日线（退市股 baostock 补齐）→ 因子面板（PIT 过滤）→ IC/分层检验 → 出图 | ✅ 增量 |
| 5 | `05_portfolio_backtest.py` | 组合/截面回测：多因子 Top-N 等权策略，扣费后对比基准（离线计算） | ✅ |
| 6 | `06_walkforward.py` | walk-forward 防过拟合：逐年训练选参 → 样本外评估，输出 OOS 绩效与参数稳定性 | ✅ |
| 7 | `07_ledger_demo.py` | 模拟盘账本演示：写入当日记录（快照/持仓/成交）+ 每日备份 | ✅ |
| 8 | `08_daily_pipeline.py` | **影子盘每日流水线（T+1 两段式）**：收盘挂单→次日开盘撮合（涨跌停/停牌拒单）→账本→日报→（可选）邮件推送 | ✅ 本地 |
| 9 | `09_test_mail.py` | 邮件推送连通性测试（配置 `.env` 后运行） | ✅ |
| 10 | `10_daily_run.py` | **每日一键**：更新 PIT 全量行情 → 影子盘流水线（large 100万 + small 2000元）→ 邮件 | ✅ |
| 11 | `11_neutralize_factors.py` | 因子市值中性化检验（IC/NW-t 前后对比） | ✅ |
| 12 | `12_fetch_dividends.py` | 分红送配增量抓取（baostock，可断点续跑） | ✅ |
| 14 | `14_scheduler.py` | **Python 原生调度器（APScheduler）**：每天 19:00 运行 02b + 10，需常驻；`--run-now` 立即跑一次 | ✅ |
| 16 | `16_webapp.py` | **本地可视化控制台**（零依赖）：账户/持仓/涨跌/待成交/人工挂单/市场状态/日报/后台任务 | ✅ |
| 17 | `17_migrate_to_daily_partitions.py` | 存储迁移：per-symbol 日线 → 按日分区（一次性） | ✅ |
| 18 | `18_rebuild_daily_cache.py` | 从按日分区重建本地股票缓存（新克隆后运行） | ✅ |
| 19 | `19_audit_numbers.py` | **数字审计**：净值恒等式/现金连续性/成交金额/持仓权重/日报一致性 | ✅ |
| d | `demo_analysis.py` | 单标的演示：质检 → 概览 → 分年度 → 双均线回测 → 出图 | ✅ |

## 定时任务（每日自动跑）

| 文件 | 用途 |
|---|---|
| `run_daily.bat` | 任务计划实际调用的包装脚本（切目录 + 日志重定向到 `data/store/logs/daily_run.log`）；**三段式（D-090）**：`02b --skip-minute`（日线/指数）→ `10 --skip-update`（四账户 + **邮件在此发出**）→ `02b --minute-only`（5 分钟线，10~40 分钟，放在邮件之后）；**每段写带时间戳的头（D-101）**——旧日志无日期，11-23 任务未触发后无法追责 |
| `54_backfill_day.py` | **影子盘缺口回填（D-101）**：某交易日 19:00 任务没跑时，把那天的快照/日报按当天收盘价补齐并补发邮件（幂等，auto 自动找缺口日）。**日更链已内置自动补流水（D-102）**——19:00 任务或控制台 daily 会自己追平缺口，此脚本用于历史缺口/独立补救 |
| `13_setup_task.ps1` | 一键安装/卸载 Windows 任务计划（每天 19:00，错过补跑） |

安装：右键 `13_setup_task.ps1` →「使用 PowerShell 运行」；或命令：
`powershell -ExecutionPolicy Bypass -File scripts\13_setup_task.ps1`

**可移植性**（2026-11-12）：`run_daily.bat` / `run_webapp.bat` 不含任何本机路径——共享引导
`_bootstrap.bat` 自动定位仓库根，并按以下顺序解析 Python 解释器：

1. 环境变量 `QUANTDEX_PYTHON`（python.exe 全路径，显式指定）
2. `scripts\python_path.local.bat`（本机覆盖文件，已 gitignore，写 `set "PY=..."`）
3. 仓库根 `.venv\Scripts\python.exe`（普通 venv）
4. 常见 conda 位置（`%USERPROFILE%` 与 `C:\ProgramData` 下的 miniconda3/anaconda3/miniforge3）的 `QuantDex` 环境
5. PATH 上 `conda` 所在目录反推的 `envs\QuantDex`
6. 兜底：PATH 上的 `python`（README 推荐先 `conda activate QuantDex`）

换机器克隆后通常无需任何配置即可直接用；探测不到时用第 1/2 种方式指定。

## 辅助工具

| 脚本 | 用途 |
|---|---|
| `net_diag.py` | 数据源连通性诊断（代理环境 / 东财 / 新浪 / baostock），抓取异常时先跑它 |
| `make_demo_pack.py` | 生成 demo 数据包 `demo/store/`（5 只×2 年 qfq 日线，~0.24MB，随仓库提交，D-082） |
| `demo_quickstart.py` | **30 秒离线出图**：指向 demo 数据包（QUANTDEX_STORE_DIR 隔离），不碰真实仓库、不需要网络 |
| `_bootstrap.py` | 把项目根目录加入 `sys.path` 的引导模块（被其他脚本 import） |

## 常用命令

```bash
python scripts/demo_quickstart.py                # 30 秒离线出图（demo 数据包，clone 后即可跑）
python scripts/00_fetch_pit_constituents.py      # 第 0 步：PIT 成分表
python scripts/00_fetch_calendar.py              # 补交易日历（让增量跳过生效）
python scripts/04_factor_research.py             # 因子研究（首次约 20~40 分钟，可断点续跑）
python scripts/04_factor_research.py 000905      # 中证500
python scripts/demo_analysis.py 600519           # 单标的演示
```

## 说明

- 04 脚本的「抓取阶段」是增量的：有本地数据的标的只抓最后日期之后的增量；**缺 `trade_calendar` 时无法判断"已最新"，会对全部标的逐个发一次补数请求**（体感像从头跑），先跑 `00_fetch_calendar.py` 修复；
- 行情更新的「落库日期」（D-080）：交易日 09:15~15:00（含集合竞价）不落库；**开盘前（00:00~09:15）落库到上一个交易日**——19:00 漏跑时，次日开盘前补跑能把昨天补回来（账户快照也能补）；若拖到次日收盘后才补，快照只能写最新那天，漏掉的那天由控制台自动改走区间重抓（`02b`）补行情，但账户净值会缺那一天；
- 04 的「计算阶段」（面板 + IC 检验）每次都从 2018 起全量重算，这是设计使然（新增一个交易日就会改变所有因子值）；
- 结果输出：`data/store/factor_results/`（parquet + plots/ 下的 PNG）。

## 测试

```bash
python tests/run_tests.py     # 回归测试（引擎口径/成本/T+1/风控/账本，零依赖）
```
