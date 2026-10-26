"""QuantDex 全局配置：路径与默认参数。"""
from pathlib import Path

# 项目根目录（本文件所在目录）
BASE_DIR = Path(__file__).resolve().parent

# 数据目录
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"           # 旧版裸 CSV
STORE_DIR = DATA_DIR / "store"       # 新版 Parquet 数据仓库

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
GEM_PREFIX = ("300", "301", "688")  # 创业板/科创板（±20%）
