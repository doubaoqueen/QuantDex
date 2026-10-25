# QuantDex 骨架审查（2026-10-26）

> 范围：本地 `main` 工作区（前端面板 / 表达式工厂 / 按日分区 / 多账户影子盘）。
> 只看「骨架完整性与缺口」，不重复研究结论。按严重度 P0→P3 排序，标注 `文件:行`。

## 一、这版骨架值得肯定的地方

- **前端面板完成度高，不是半成品**：4 账户策略分组、主题色（盈亏联动）、净值 vs 沪深300+回撤、回测/walk-forward、调仓审批、费用明细、候选池、红绿灯、任务日志、15 秒轮询、切换竞态保护。23 个前端 API 调用与 `16_webapp.py` 后端路由**全部对得上，没有死接口**。
- **开发链路闭环**：`vite.config.js` 已配 `/api` 代理到 8765；`package-lock.json` 入库。
- **表达式工厂完整闭环**：`expr.py`（递归下降解析器）→ `expr_factors.py`（AlphaLite32）→ `22_factor_factory.py`（IC 筛选）→ `experiments.py`（存档，绑 git commit）。`build_factor_panel(expr_factors=...)` 已接通（`factors.py:100`）。"新因子写表达式不写 pandas"的纪律好。
- **快照设计扎实**：`snapshot.py` 一次请求替 949 次、盘中不落库、成交量手→股、失败回退 baostock。
- **证伪文化**：regime 入场过滤（D-022）、个股止损（D-026）回测负贡献就不启用、只留预警——这是真研究，不是凑结果。

## 二、骨架断裂（P0：影响"开箱即用"或"结果可信"）

### P0-1 克隆后必读的一步，README 没写（项目一拉下来就是坏的）
- `daily/`（per-symbol 缓存）已被 `.gitignore`（第 244 行），clone 下来只有 `daily_by_date/` 分区。
- 但所有 `get_kline`、`16_webapp` 的 overview/positions/regime/candidates **都读 `daily/`**。
- 不先跑 `python scripts/18_rebuild_daily_cache.py`，整个项目读不到行情，webapp 全空/报错。
- **修复**：README「三步上手」最前面加一步"克隆后重建缓存"；并在 `16_webapp` 启动时检测 `daily/` 为空就提示（甚至自动调一次 `rebuild_symbol_cache`）。
- `TODO.md:122` 自己记了这条，但 README 没落地。

### P0-2 README 整体停留在 v0.2，与代码严重脱节
- 能力总览/目录结构没有：`frontend/`、`16_webapp.py`、`expr/expr_factors/experiments`、`partitions/snapshot`、`live/` 影子盘、`02b`、`10~22` 脚本（脚本只列到 08）。
- 没有前端说明：`cd frontend && npm install && npm run build`（或直接跑 16_webapp 用已入库的 dist）、Node 版本、dev 模式 `5173`。
- 目录结构里财务路径、`financials/abstract|baostock` 等已过时。
- **修复**：README 升到 v0.3，补"前端面板""影子盘""表达式工厂""克隆初始化"四节；目录树按实际重画。

### P0-3 回测口径和实盘口径仍然不一致（真金白银）
- `src/backtest/portfolio.py:8` 明写 **"T+1 收盘执行"**，输入 `prices` 是收盘价。
- `src/live/pipeline.py`、前端 footer（`App.vue:276`）、README 都声称 **"T+1 开盘撮合"**。
- 结果：回测数字（+31.67%、OOS 8.26%、div 82%、topk 53%）全是收盘价口径，影子盘却按开盘价跑，**回测对实盘有系统性偏差**。TODO P0-3 只统一了 pipeline 内部两段式，没动回测。
- 同处涨停判定 `portfolio.py:147` 容差 `tol=0.006` 且用收盘涨跌幅：涨 9.4% 就被误判涨停买不进（实际没封板）。
- **修复**：回测引擎支持"开盘价执行"模式（用 open 价做 T+1 调仓），与 pipeline 完全对齐；涨停判定改为执行日开盘集合竞价是否封板，容差收紧到 0.2%。

### P0-4 手工下单/撮合无资金、持仓、T+1 校验
- `16_webapp.py:482 add_order` 与 `pipeline._fill_pending`：buy 不查现金、sell 不查持仓股数、不拦"当日买入当日卖出"。
- 人工可以超额买入（高开会负现金）、卖空、触发 T+1 违规。这恰是半自动审批的核心入口，却最不设防。
- `TODO.md:91` 已列未做。
- **修复**：挂单前按"最坏开盘价 + 费用"校验现金；卖出校验可用持仓（扣除当日买入）；账本层加硬约束而非仅靠前端。

### P0-5 `/api/report` 路径遍历可读任意文件（含 `.env`）
- `16_webapp.py:565 report_text` 直接 `STORE_DIR/"reports"/acc/f"{date}.md"` 后 `read_text`，`acc`/`date` 来自 query，**没有任何校验**。
- 传 `account=..` 或 `date=../../../.env` 可读到 SMTP 授权码等。`reports()` 同样可被 `../` 遍历列目录。
- 对比：静态文件 `_serve_static:47` 做了 `startswith(base)` 防护，API 却没做——防护不一致。
- 虽绑定 127.0.0.1，但浏览器里任意网页可对 localhost 发 POST（无 token / 不校验 Origin），下单、取消、跑流水线都可能被 CSRF 触发。
- **修复**：
  ```python
  if acc not in ACCOUNTS: return {"err": "bad account"}
  import re
  if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)): return {"text": "bad date"}
  ```
  并对 POST 校验 `Origin/Referer`，或启动时生成随机 token 由前端携带。

## 三、工程卫生（P1）

### P1-1 数据仍在 git，且体积翻倍、文件数爆炸
- `git gc` 后仍 **325MB**（上次审查 146MB）；`data/store` 约 **5700+ 文件**（market 3800、financials 1795）。
- 按日分区把"改几百个大文件"变成"每天加一个小文件"，**只解决了 git diff 噪音，没解决体积/克隆/二进制膨胀**；海量小文件还带来 clone 时的小文件 IO 开销。
- 财务 1795 个 per-symbol 文件仍是重写式入库（没分区）。
- 根本解仍是 **数据出 git**（`TODO.md:103` 列了未做）：代码 + `meta/` 等慢变参考数据入库，行情/财务/结果走 Release 附件或一键重建脚本。
- 前端 `dist/`（1.1MB 打包 JS）也入库，前端一改就是二进制膨胀；建议 dist 也 gitignore，发布时 build（若坚持零 Node 可跑，需在 README 写明取舍）。

### P1-2 分区层是"外挂"，双轨存储 + 静默失败
- `ParquetStore` 的 kind 体系只认 `daily/`（缓存），`daily_by_date/` 由 `partitions.py` 独立维护，未纳入统一抽象；写时双写、读时只读缓存。
- `storage.py:114` 和 `:185` 把分区写入包在 `except Exception: pass` 里——**分区写失败你完全不知道，入库数据可能缺**。
- `partitions.py:98` 残留一行死代码 `... if False else None`，下方又重复实现一遍循环。
- **修复**：分区写入失败至少 log；删掉 98 行死代码；长期把分区并入 store 的读写抽象，别让调用方感知两套路径。

### P1-3 webapp 后端 40KB 零测试
- `tests/` 有 7 个文件，没有 `test_webapp`：路由、账户参数校验、下单/取消、路径遍历、金额计算都无回归。
- 至少补：非法 account/date 被拒、订单数量必须 100 整数倍、现金不足拒绝、静态路径不越界。

### P1-4 依赖与配置
- `requirements.txt` 仍全 `>=` 不锁版本（`TODO.md:14` 未做）；前端有 lock，后端反而没有。用 `pip freeze > requirements.lock` 或 `conda env export`。
- 费率/本金/股票池仍散落硬编码（`16_webapp.add_order` 里费率写死 0.00025/0.00001/0.0005），集中到 `config.yaml`（TODO 已列）。
- 无 CI（TODO 已列）。

### P1-5 待清理 / 信息
- 根目录 4 个调试脚本 `_chartdbg.py _navcheck.py _rally.py _regime.py` 应删除或移进 `scripts/scratch/`。
- `data/store/_legacy_old_naming/` 6 个旧文件（`TODO.md:13` 两轮未删）。
- 脚本编号缺 15（15_dashboard 退役后跳号，可在 README 注明）。
- `TODO.md:25` 真实 QQ 邮箱仍在公开仓库（`.env` 授权码已正确忽略，这点对）。
- `16_webapp.py:655-769` 内嵌 115 行旧版单文件 HTML（PAGE），dist 已入库后是**永不执行的死代码**，且只认 large/small，建议删除或明确为兜底。

## 四、架构演进建议（P2：多策略扩张前先做）

### P2-1 缺统一的"策略注册表"
- 现在每加一个策略要改 N 处：`16_webapp` 的 `ACCOUNTS`、`acc2suffix`、`candidates` 里的 `if is_div`、`multi_factor` 权重函数、回测产物后缀、前端 `App.vue` 账户列表。
- 已经有 4 个账户（large/small/div/topk），还要加 S2/S3/S5。建议一张策略注册表：
  `strategy_key -> {权重函数, 回测产物后缀, 账本文件, 前端标签, 因子集合}`，账户与策略解耦。
- 否则策略越多，硬编码分叉越多，越容易出现"账户对不上回测"的错。

### P2-2 webapp 性能
- `candidates()` 缓存失效时在 HTTP 请求里同步遍历几百只股票读 parquet 算因子（`16_webapp.py:256`），首次可能数十秒。应改由每日流水线预生成候选池 parquet，webapp 只读。
- `regime()` 市场宽度 `16_webapp.py:541` 读 `market/daily/`（缓存，可被删）且 `[:200]` 取文件系统前 200 个、不是固定股票池，样本不稳定、缓存缺失时静默为 None。应读 `daily_by_date` 最新分区 + 固定 PIT 股票池。

### P2-3 表达式引擎表达能力
- `expr.py` 只有时序算子，缺**横截面算子**（`Rank`/`GroupRank`/`Scale`）——横截面选股的核心 rank 现在靠外层补，表达式无法表达"全市场相对排名"。
- 负窗口 `Ref(x, n<0)`（未来值）靠注释约定"只能进标签"，没有机制拦截；建议求值时区分 `feature`/`label` 上下文，特征里出现负 Ref 直接抛错。

## 五、优先级一览

| 级别 | 事项 |
|---|---|
| P0 | README 补"克隆后跑 18 重建缓存"；README 升级到当前版本 |
| P0 | 回测改 T+1 开盘价、与实盘对齐；涨停判定改开盘/收紧容差 |
| P0 | 下单资金/持仓/T+1 校验 |
| P0 | `/api/report` 路径遍历修复 + POST 来源校验/CSRF token |
| P1 | 数据出 git（325MB / 5700 文件）；dist 一并考虑 |
| P1 | 分区失败不要静默；删 `partitions.py:98` 死代码 |
| P1 | 补 test_webapp；锁后端依赖版本 |
| P1 | 清 `_*.py` 调试脚本 / _legacy / 内嵌旧 PAGE；抹 QQ 邮箱 |
| P2 | 策略注册表；候选池预生成；regime 宽度改分区+固定池 |
| P2 | 表达式引擎补截面算子 + 未来值拦截 |

## 六、一句话总结

研究链路（PIT → 因子 → 表达式工厂 → walk-forward → 多账户影子盘 → 面板）已经成型且有证伪纪律，**骨架的"脑"和"脸"都长出来了；但"开箱即用"的接口（README 初始化）、"回测=实盘"的一致性、以及面板这层的安全/校验还没跟上**。先修 P0 五条——它们分别决定"别人能不能跑起来""数字对不对得上""面板能不能放心点"。
