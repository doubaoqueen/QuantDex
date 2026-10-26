# QuantDex 代码规范（D-044，2026-10-27 起强制）

> 缘起：用户裁决推翻 D-039 的"不做整体重构"——项目将开源供人借鉴，
> 且 Python 的"随便"必须用成文规范约束。本文档 = 唯一标准，新代码强制，
> 存量代码按"批次迁移计划"（§8）逐步对齐。

## 1. 目录职责（哪里只能放什么）

| 路径 | 只允许放 | 禁止 |
|---|---|---|
| `src/data/` | 抓取、清洗、存储、标准化（一个数据源/主题一个模块） | 策略逻辑、展示逻辑 |
| `src/analysis/` | 因子计算、检验引擎、表达式、实验存档、教学内容数据 | 网络 IO、账本 |
| `src/strategies/` | 策略函数与**策略注册表**（唯一账户来源） | 数据抓取、UI |
| `src/backtest/` | 回测引擎与绩效（纯计算，无 IO 副作用） | 抓取、展示 |
| `src/risk/` | 组合级风控 | 个股逻辑 |
| `src/live/` | 影子盘：账本/流水线/通知/锁 | 研究逻辑 |
| `scripts/` | **只做编排**：调 src 函数 + CLI 参数 + 打印。编号 = 执行顺序 | 业务逻辑（超过 50 行逻辑必须下沉 src） |
| `frontend/src/` | Vue3 组件；**页面 HTML 一律在此** | Python 生成 HTML（D-044 起禁止） |
| `docs/` | 决策/规范/设计文档 | — |
| `quantdex_vault/` | 知识库（本地，不入库） | — |
| 根目录 | 只放 config.py、README、TODO 等入口文件 | 任何脚本、临时文件 |

## 2. 命名

- **模块/文件**：`snake_case.py`；脚本 `NN_动词_宾语.py`（如 `02b_update_pit.py`）；
- **函数**：`snake_case`，动词开头（fetch_/build_/run_/check_/record_）；
  策略函数命名 = `因子组合_选股法`（如 `turnover_lowvol_dividend_topn`）；
- **常量**：`UPPER_SNAKE`，定义在模块顶部或 config.py，**禁止裸魔法数字**
  （费率/阈值/端口必须具名常量或 config）；
- **类**：`PascalCase`；**私有**：`_` 前缀；
- **SQL 列别名禁止与 Python 变量同名**（R3-41 教训：`SUM(fee) AS f` + 变量 f
  曾引发静默 KeyError）——别名用全称（commission/stamp/amount）。

## 3. 文档字符串（强制）

- 所有公开函数/类必须有 docstring：**第一行中文一句话**；口径类决策
  （窗口/执行价/复权）必须写进 docstring 并引用决策编号（如 "见 D-038"）；
- 策略函数额外注明：因子构成、检验结论（引用 D-xxx）、适用场景；
- 注释只写"为什么"，不写"做了什么"（代码自明）。

## 4. 类型与异常

- 公开函数签名加类型标注（渐进：新代码 100%，存量触碰时补）；
- **禁止宽泛 `except Exception: pass`**——必须捕获后可见（print/logging）或
  抛出；能预见的异常用具体类型；
- 数据写入路径禁止静默失败（分区写入失败必须告警，D-034 先例）。

## 5. 数据与口径铁律（沿用并集中）

- 复权口径隔离、PIT 对齐、单位量纲、口径三处一致（检验/回测/实盘）——
  细节见 docs/DATA_ROADMAP.md §5 与 D-038/D-041；
- 任何窗口/窗口收益计算**禁止按行位置跨洞**（用交易日序号校验，D-038）。

## 6. 配置

- 全局常量进 `config.py`；账户/策略定义**唯一来源 = src/strategies/registry.py**；
- 环境变量覆盖：`QUANTDEX_*` 前缀；凭证只进 .env（gitignore）；
- 新增配置禁止散落模块内（魔法数字清零是 D-044 验收项）。

## 7. 前端（Vue3 + Tailwind）

- 页面/组件在 `frontend/src/`；**Python 端只出 JSON API**（16_webapp 是数据层，
  不是页面层）；
- 组件名 PascalCase；每个卡片/面板一个组件；展示逻辑（染色/格式化）用
  computed/工具函数，不内联长表达式；
- 构建产物 `frontend/dist` 随 git 提交（服务端只托管静态 + JSON）。

## 8. 测试与验证

- 新功能必须带回归测试（tests/test_*.py，风格随现有 43+ 项）；
- 修复 bug 必须先写"复现该 bug 的测试"再修；
- 口径/金额类改动跑全量 `tests/run_tests.py` + `19_audit_numbers.py`。

## 9. 批次迁移计划（存量对齐，倒排）

| 批次 | 内容 | 状态 |
|---|---|---|
| 已完成 | 策略注册表、CI、表达式注册、requirements 锁定 | ✅ |
| 本轮 | 量化学院迁入 Vue3（废除 Python 教学页）、CODE_STANDARD 立档 | ✅/进行中 |
| 下批 | ruff + ruff.toml 全量 lint 清零、类型标注存量扫描、config.py 收编魔法数字 | 排期 |
| 触发式 | 存量函数 docstring 补齐（触碰时补）、24 学院数据下沉 src/analysis | 随触碰 |
