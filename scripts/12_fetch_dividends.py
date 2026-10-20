# -*- coding: utf-8 -*-
"""
增量抓取 PIT 历史成员的分红送配数据（baostock，按报告年度）。
已存在的文件自动跳过，可断点续跑。

用法：
    python scripts/12_fetch_dividends.py            # 抓全部（跳过已有）
    python scripts/12_fetch_dividends.py 50         # 只抓前 50 只（调试）
输出：data/store/fin_bs/{code}__dividend.parquet
"""
import sys

import _bootstrap  # noqa: F401

from config import STORE_DIR
from src.data import universe
from src.data.financials import fetch_dividend
from src.data.storage import ParquetStore

YEARS = 8


def main() -> None:
    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if limit:
        symbols = symbols[:limit]
    print(f">> 分红抓取：{len(symbols)} 只（已有则跳过）", flush=True)
    ok = skip = fail = 0
    for i, sym in enumerate(symbols, 1):
        try:
            if (STORE_DIR / "financials" / "baostock" / f"{sym}__dividend.parquet").exists():
                skip += 1
                continue
            df = fetch_dividend(str(sym), years=YEARS, store=store)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"   FAIL {sym}: {str(e)[:80]}", flush=True)
        if i % 25 == 0:
            print(f"   进度 {i}/{len(symbols)} | 新增 {ok} 跳过 {skip} 失败 {fail}", flush=True)
    print(f">> 完成：新增 {ok}，跳过 {skip}，失败 {fail}", flush=True)


if __name__ == "__main__":
    main()
