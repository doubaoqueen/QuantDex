# -*- coding: utf-8 -*-
"""
第 0b 步：补全交易日历 meta/trade_calendar，并做数据覆盖健康检查。

为什么需要：update_kline_batch 依赖交易日历判断「已覆盖最近收盘」的标的，
缺失日历会导致每次运行都对全部标的逐个发一次补数请求（体感像从头跑）。

用法：python scripts/00_fetch_calendar.py
"""
import _bootstrap  # noqa: F401

from src.data.market import get_local_last_date, last_closed_trade_date
from src.data.storage import ParquetStore
from src.data.universe import fetch_trade_calendar, pit_union_symbols


def main() -> None:
    store = ParquetStore()

    print(">> 补全交易日历 ...")
    cal = fetch_trade_calendar(store)
    print(f"   交易日历: {len(cal)} 行, "
          f"{cal['date'].min().date()} ~ {cal['date'].max().date()}")

    lc = last_closed_trade_date(store)
    print(f"   最近已收盘交易日: {lc}")

    # ---- 健康检查：PIT 并集 vs 本地日线覆盖 ----
    try:
        symbols = pit_union_symbols("000300", store)
    except FileNotFoundError:
        print("   (无 PIT 成分表, 跳过覆盖检查；可先运行 00_fetch_pit_constituents.py)")
        return

    missing = [s for s in symbols if get_local_last_date(s, "daily", "qfq", store) is None]
    print(f"   PIT 并集 {len(symbols)} 只 | 本地缺日线 {len(missing)} 只")
    if missing:
        print("   缺失(前 20):", missing[:20])
        print("   → 重跑 04 脚本会自动补抓（退市股走 baostock）")


if __name__ == "__main__":
    main()
