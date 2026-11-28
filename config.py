"""QuantDex 全局配置：路径与默认参数。"""
import os
from pathlib import Path

# 项目根目录（本文件所在目录）
BASE_DIR = Path(__file__).resolve().parent

# 数据目录
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"           # 旧版裸 CSV
# 新版 Parquet 数据仓库；QUANTDEX_STORE_DIR 可整体换仓（demo pack 隔离演示用，D-082）
STORE_DIR = Path(os.environ.get("QUANTDEX_STORE_DIR") or (DATA_DIR / "store"))

# 数据仓库分区目录
MARKET_DIR = STORE_DIR / "market"
META_DIR = STORE_DIR / "meta"
VALUATION_DIR = STORE_DIR / "valuation"
FINANCIAL_DIR = STORE_DIR / "financials"
MONEYFLOW_DIR = STORE_DIR / "moneyflow"
MACRO_DIR = STORE_DIR / "macro"

# 数据注册表（记录每个数据集的范围、行数、更新时间）
REGISTRY_PATH = STORE_DIR / "registry.parquet"

# 默认抓取参数
DEFAULT_START_DATE = "20150101"     # 默认日线起始
TRADING_DAYS_PER_YEAR = 250         # 年化交易日

# 网络请求重试
MAX_RETRIES = 3
RETRY_SLEEP = 1.5

# 批量抓取时的请求间隔（秒），避免被数据源限流
REQUEST_INTERVAL = 0.35


def ensure_dirs() -> None:
    """启动时确保所有目录存在。"""
    for d in [
        MARKET_DIR / "daily",
        MARKET_DIR / "weekly",
        MARKET_DIR / "monthly",
        MARKET_DIR / "minute",
        META_DIR,
        VALUATION_DIR,
        FINANCIAL_DIR / "abstract",
        FINANCIAL_DIR / "baostock",
        MONEYFLOW_DIR,
        MACRO_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


# 交易成本与撮合参数（影子盘与回测共用，D-044 收编）
FEE_RATE = 0.00025        # 券商佣金率（单边）
MIN_FEE = 5.0             # 最低佣金（元/笔）
SLIP_RATE = 0.0005        # 滑点（单边，按成交额估算）
STAMP_RATE = 0.0005       # 印花税（仅卖出）
TRANSFER_RATE = 0.00001   # 过户费（双边，0.001%）
LOT = 100                 # A 股整手
GEM_PREFIX = ("300", "301")         # 创业板
STAR_PREFIX = "688"                 # 科创板

# 历史费率时间线（D-118）：回测按**成交日**取当期法定费率；影子盘撮合永远用上面的现行值。
# 条目 = (生效日, 费率)，日期升序，None = 自始适用。
#   印花税（仅卖出）：2008-09-19 起 0.1% → 2023-08-28 减半
#   过户费（双边）：  2015-08-01 起 0.002% → 2022-04-29 减半
# 早先回测全程按现行值计，2018~2023-08 的卖出少算一半印花税（实测约 1.5pp 总收益）。
STAMP_RATE_HISTORY: tuple[tuple[str | None, float], ...] = (
    (None, 0.0010), ("2023-08-28", STAMP_RATE))
TRANSFER_RATE_HISTORY: tuple[tuple[str | None, float], ...] = (
    (None, 0.00002), ("2022-04-29", TRANSFER_RATE))

# 基准全收益近似（D-096 强制口径①，D-118 落地到 05/06）：价格指数 + 固定股息率/年。
# 免费源没有沪深300 全收益指数（H00300）的历史，故用近似；股息率年际在约 1.8%~3.5%
# 间波动，取 2% 是 D-096 的既定口径——偏高或偏低 1pp，年化超额同向差约 1pp。
BENCH_DIVIDEND_YIELD = 0.02

# 制度时间线（涨跌停幅度随年代不同，回测/撮合必须按**日期**判定）：
#   创业板 2020-08-24 注册制改革：±10% → ±20%（ST 同升）；此前 ST ±5%
#   科创板 2019-07-22 开市即 ±20%（含 ST；上市前 5 日无涨跌停，未建模）
#   北交所 2021-11-15 开市 ±30%（4/8/92 号段；开市前属新三板，本系统不覆盖）
GEM_REG_START = "2020-08-24"


def _as_iso_date(date) -> str | None:
    """date 归一化为 'YYYY-MM-DD' 字符串（ISO 字符串比较即时间序）；无法解析返回 None。"""
    if date is None:
        return None
    try:
        s = date.strftime("%Y-%m-%d")             # pd.Timestamp / datetime
    except AttributeError:
        s = str(date).strip()
        if len(s) == 8 and s.isdigit():           # YYYYMMDD
            s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        else:
            s = s[:10]
    return s if len(s) == 10 else None


def board_limit(symbol: str, name: str | None = None, date=None) -> float:
    """涨跌停幅度（撮合 / 回测 / 控制台展示共用，唯一来源）。

    date=None（实盘快照 / 当日展示）→ 现行规则；
    date=成交日（回测/撮合传真实交易日）→ 按当年制度判定——修复深检
    2026-11-22 发现的时代错位：2018~2020-08 的创业板旧实现恒按 ±20% 判定
    （真实 ±10%），那段样本里"涨停买不进/跌停卖不出"的保真约束静默失效。

    规则：
      - 北交所（4/8/92 开头）±30%；
      - 科创板 688 ±20%（含 ST）；
      - 创业板 300/301：2020-08-24 起 ±20%（含 ST），此前 ±10%（ST ±5%）；
      - 主板 ±10%，ST ±5%。
    ST 依据证券简称（需调用方传 name；不传按非 ST 处理——回测侧拿不到名称时，
    历史 ST 股会按非 ST 幅度判定，属已知边界。注：F-21 修复后撮合侧 pipeline
    已传名，方向与旧注释相反；回测侧 portfolio 仍不传名）。
    """
    c = str(symbol)
    is_st = False
    if name:
        nm = str(name).upper().replace(" ", "")
        is_st = nm.startswith(("*ST", "ST"))
    d = _as_iso_date(date)
    if c.startswith(("4", "8", "92")):            # 北交所（含新三板老号段）
        return 0.30
    if c.startswith(GEM_PREFIX):                  # 创业板
        if d is None or d >= GEM_REG_START:
            return 0.20                           # 注册制后：含 ST 均 ±20%
        return 0.05 if is_st else 0.10
    if c.startswith(STAR_PREFIX):                 # 科创板：含 ST 均 ±20%
        return 0.20
    return 0.05 if is_st else 0.10                # 主板


def rate_on(history: tuple[tuple[str | None, float], ...], date=None) -> float:
    """取某日适用的费率：生效日 <= date 的最后一个条目（date=None → 现行值，即末条）。

    history 形如 STAMP_RATE_HISTORY；date 早于全部生效日时返回首条（自始费率）。
    """
    d = _as_iso_date(date)
    rate = history[0][1]
    for effective, r in history:
        if effective is None or d is None or effective <= d:
            rate = r
    return rate
