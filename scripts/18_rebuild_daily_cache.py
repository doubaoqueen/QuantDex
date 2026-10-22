# -*- coding: utf-8 -*-
"""
从按日分区重建本地股票缓存（新克隆仓库后运行一次）。

背景：data/store/market/daily/ 是派生缓存（已 gitignore），
      data/store/market/daily_by_date/ 是 git 提交的唯一行情数据。
用法：python scripts/18_rebuild_daily_cache.py
"""
import _bootstrap  # noqa: F401

from src.data.partitions import rebuild_symbol_cache
from src.data.storage import ParquetStore


def main() -> None:
    r = rebuild_symbol_cache(ParquetStore())
    print(f">> 重建完成：{r['symbols']} 个股票文件，{r['rows']} 行", flush=True)


if __name__ == "__main__":
    main()
