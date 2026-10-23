# -*- coding: utf-8 -*-
"""
只更新 PIT 历史成员行情（不跑流水线）。

用途：
  - 定时任务的第一步（与影子盘流水线解耦：更新失败不影响账户记录）；
  - 默认走 baostock（source=bs，实测 949 只约 3~5 分钟、失败 0，见 D-012：
    东财对本机逐只请求失败，含重试约 7.8 秒/只）；
  - 新浪降级默认禁用（QUANTDEX_SINA_FALLBACK=1 才启用）：其依赖 py_mini_racer(V8)，
    在本机会原生段错误（D-008）。
  - 可用环境变量切换：QUANTDEX_SOURCE=em（东财）/ bs（baostock，默认）。

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
    source = os.environ.get("QUANTDEX_SOURCE", "bs")
    allow_sina = os.environ.get("QUANTDEX_SINA_FALLBACK", "0") == "1"
    allow_bs = os.environ.get("QUANTDEX_BS_FALLBACK", "1") == "1"
    print(f">> 更新 PIT 行情：{len(symbols)} 只 | source={source} | "
          f"新浪降级={allow_sina} | baostock兜底={allow_bs}", flush=True)
    r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                           full_start=DEFAULT_START_DATE, source=source,
                           store=store, workers=4, sina_fallback=allow_sina,
                           bs_fallback=allow_bs)
    failed = r.pop("__failed__", [])
    n_up = sum(1 for v in r.values() if v == "uptodate")
    n_bs = sum(1 for v in r.values() if isinstance(v, str) and v.startswith("bs_fallback"))
    n_sina = sum(1 for v in r.values() if isinstance(v, str) and v.startswith("sina_fallback"))
    print(f">> 完成：处理 {len(r)} 只（已最新 {n_up}、baostock兜底 {n_bs}、新浪兜底 {n_sina}），"
          f"失败 {len(failed)} 只", flush=True)
    for code, err in failed[:10]:
        print(f"   FAIL {code}: {str(err)[:100]}", flush=True)

    # 基准指数不在股票池里，必须单独更新（否则报告/市场状态一直是旧值）
    from src.data.snapshot import update_indexes
    print(">> 更新基准指数（沪深300 / 中证500）...", flush=True)
    ir = update_indexes(store)
    print(f">> 指数完成：{len(ir)} 个", flush=True)


if __name__ == "__main__":
    main()
