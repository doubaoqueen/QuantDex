"""
第 1 步：初始化标的池与基础数据（股票/ETF/指数/可转债列表、交易日历、
全量证券、主要指数成分股）。
用法：python scripts/01_init_universe.py
"""
import _bootstrap  # noqa: F401

from src.data.sources import bs_logout
from src.data.storage import ParquetStore
from src.data.universe import init_universe


def main() -> None:
    store = ParquetStore()
    print(">> 开始初始化标的池 ...")
    result = init_universe(store)
    print("\n>> 初始化完成：")
    for k, v in result.items():
        print(f"  {k}: {v} 条")
    bs_logout()


if __name__ == "__main__":
    main()
