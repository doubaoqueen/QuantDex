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
| 12 | `12_fetch_dividends.py` | 分红送配增量抓取（baostock，可断点续跑） | ⏳ 进行中 |
| d | `demo_analysis.py` | 单标的演示：质检 → 概览 → 分年度 → 双均线回测 → 出图 | ✅ |

## 定时任务（每日自动跑）

| 文件 | 用途 |
|---|---|
| `run_daily.bat` | 任务计划实际调用的包装脚本（切目录 + 日志重定向到 `data/store/logs/daily_run.log`） |
| `13_setup_task.ps1` | 一键安装/卸载 Windows 任务计划（每天 19:00，错过补跑） |

安装：右键 `13_setup_task.ps1` →「使用 PowerShell 运行」；或命令：
`powershell -ExecutionPolicy Bypass -File scripts\13_setup_task.ps1`

## 辅助工具

| 脚本 | 用途 |
|---|---|
| `net_diag.py` | 数据源连通性诊断（代理环境 / 东财 / 新浪 / baostock），抓取异常时先跑它 |
| `_bootstrap.py` | 把项目根目录加入 `sys.path` 的引导模块（被其他脚本 import） |

## 常用命令

```bash
python scripts/00_fetch_pit_constituents.py      # 第 0 步：PIT 成分表
python scripts/00_fetch_calendar.py              # 补交易日历（让增量跳过生效）
python scripts/04_factor_research.py             # 因子研究（首次约 20~40 分钟，可断点续跑）
python scripts/04_factor_research.py 000905      # 中证500
python scripts/demo_analysis.py 600519           # 单标的演示
```

## 说明

- 04 脚本的「抓取阶段」是增量的：有本地数据的标的只抓最后日期之后的增量；**缺 `trade_calendar` 时无法判断"已最新"，会对全部标的逐个发一次补数请求**（体感像从头跑），先跑 `00_fetch_calendar.py` 修复；
- 04 的「计算阶段」（面板 + IC 检验）每次都从 2018 起全量重算，这是设计使然（新增一个交易日就会改变所有因子值）；
- 结果输出：`data/store/factor_results/`（parquet + plots/ 下的 PNG）。

## 测试

```bash
python tests/run_tests.py     # 回归测试（引擎口径/成本/T+1/风控/账本，零依赖）
```
