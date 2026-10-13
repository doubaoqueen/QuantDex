# -*- coding: utf-8 -*-
"""
第 2 步：批量（增量）抓取核心标的日线并落盘。
已有数据的标的只增量抓最新部分，可反复运行。
用法：python scripts/02_update_market.py
"""
import _bootstrap  # noqa: F401
from src.data.storage import ParquetStore
from src.data.market import update_kline_batch
from src.data.sources import bs_logout

# 默认跟踪清单（可自行增删，代码为 6 位）
STOCKS = ["600519", "000858", "601318", "000333", "300750"]
ETFS = ["510300", "510500", "512100", "588000", "159915",
        "518880", "513100", "513050"]
INDEXES = ["000300", "000905", "000852", "000016", "399006"]


def main() -> None:
    store = ParquetStore()
    print(">> 更新个股日线 ...")
    r1 = update_kline_batch(STOCKS, asset="stock", store=store)
    print(">> 更新 ETF 日线 ...")
    r2 = update_kline_batch(ETFS, asset="etf", store=store)
    print(">> 更新指数日线 ...")
    r3 = update_kline_batch(INDEXES, asset="index", adjust="", store=store)

    for name, r in (("个股", r1), ("ETF", r2), ("指数", r3)):
        failed = r.pop("__failed__", [])
        print(f"\n{name}: 成功 {len(r)} 个，失败 {len(failed)} 个")
        for code, err in failed:
            print(f"  FAIL {code}: {err}")
    bs_logout()


if __name__ == "__main__":
    main()
