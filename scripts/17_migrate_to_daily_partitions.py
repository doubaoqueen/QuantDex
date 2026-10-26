"""
一次性迁移：per-symbol 日线 → 按日分区（append-only）。

用法：python scripts/17_migrate_to_daily_partitions.py
输出：data/store/market/daily_by_date/YYYY-MM-DD.parquet（约 2132 个）
校验：行数、随机标的对比。
"""
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.partitions import PART_DIR, migrate_from_symbols, rebuild_symbol_cache
from src.data.storage import ParquetStore


def main() -> None:
    store = ParquetStore()
    print(">> 迁移 per-symbol → 按日分区 ...", flush=True)
    r = migrate_from_symbols(store)
    print(f">> 完成：{r['days']} 个分区，{r['rows']} 行", flush=True)
    files = sorted(PART_DIR.glob("*.parquet"))
    print(f">> 校验：分区文件 {len(files)} 个", flush=True)
    # 随机抽查 3 天，对比原文件行数
    sample_days = [files[0], files[len(files) // 2], files[-1]]
    for p in sample_days:
        d = pd.read_parquet(p)
        print(f"   {p.name}: {len(d)} 行, {d['symbol'].nunique()} 只", flush=True)
    # 抽查 600519 行数
    name = "600519__qfq"
    old = store.read("daily", name)
    parts = []
    for p in files:
        d = pd.read_parquet(p)
        parts.append(d[d["symbol"] == "600519"])
    new = pd.concat(parts) if parts else pd.DataFrame()
    print(f">> 600519: 原 {len(old)} 行 / 分区 {len(new)} 行", flush=True)
    if len(old) == len(new):
        print(">> 行数一致 [OK]", flush=True)
    else:
        print(">> 行数不一致，请检查！", flush=True)


if __name__ == "__main__":
    main()
