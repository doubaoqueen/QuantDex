# -*- coding: utf-8 -*-
"""
只更新 PIT 历史成员行情（不跑流水线）。

用途：
  - 定时任务的第一步（与影子盘流水线解耦：更新失败不影响账户记录）；
  - 默认走东财（source=em）并**禁用新浪降级**：本机新浪依赖 py_mini_racer(V8)，
    会原生段错误（已实测），禁用后永不触发。
  - 可用环境变量切换：QUANTDEX_SOURCE=bs（baostock，无 JS 依赖，较慢但稳）。

用法：
  python scripts/02b_update_pit.py            # 全量增量
  python scripts/02b_update_pit.py 5          # 只更新前 5 只（调试）
"""
import os
import sys

import _bootstrap  # noqa: F401

from config import DEFAULT_START_DATE
from src.data import universe
from src.data.market import update_kline_batch
from src.data.storage import ParquetStore


def main() -> None:
    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if limit:
        symbols = symbols[:limit]
    source = os.environ.get("QUANTDEX_SOURCE", "em")
    allow_sina = os.environ.get("QUANTDEX_SINA_FALLBACK", "0") == "1"
    print(f">> 更新 PIT 行情：{len(symbols)} 只 | source={source} | 新浪降级={allow_sina}", flush=True)
    r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                           full_start=DEFAULT_START_DATE, source=source,
                           store=store, workers=4, sina_fallback=allow_sina)
    failed = r.pop("__failed__", [])
    print(f">> 完成：成功 {len(r)} 只，失败 {len(failed)} 只", flush=True)
    for code, err in failed[:10]:
        print(f"   FAIL {code}: {str(err)[:100]}", flush=True)


if __name__ == "__main__":
    main()
