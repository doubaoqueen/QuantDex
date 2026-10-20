# -*- coding: utf-8 -*-
"""
每日一键：更新 PIT 历史成员行情 → 影子盘流水线（挂单/撮合/记账/日报/邮件）。

用法：
    python scripts/10_daily_run.py              # 完整每日流程
    python scripts/10_daily_run.py --no-push    # 不发送邮件

建议 Windows 任务计划每天 19:00 运行本脚本（收盘后）。
"""
import sys

import _bootstrap  # noqa: F401

from config import DEFAULT_START_DATE
from src.data import universe
from src.data.market import update_kline_batch
from src.data.storage import ParquetStore
from src.live.pipeline import run_pipeline


def main() -> None:
    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 更新 PIT 历史成员行情（{len(symbols)} 只，增量）...")
    r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                           full_start=DEFAULT_START_DATE, source="sina",
                           store=store)
    failed = r.pop("__failed__", [])
    print(f">> 行情更新完成：成功 {len(r)} 只，失败 {len(failed)} 只")
    for code, err in failed[:5]:
        print(f"   FAIL {code}: {err}")

    print(">> 运行影子盘流水线 ...")
    run_pipeline(push="--no-push" not in sys.argv)


if __name__ == "__main__":
    main()
