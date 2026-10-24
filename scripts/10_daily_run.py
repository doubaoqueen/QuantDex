# -*- coding: utf-8 -*-
"""
每日一键：更新 PIT 历史成员行情 → 影子盘流水线（三个账户）。

账户：
  - large：100 万虚拟，现行策略（低换手+低波+反转 Top50 等权）
  - small：2000 元虚拟，整手小组合（最多 3 只），实盘建仓模拟
  - div  ：100 万虚拟，红利版（低换手+低波+股息率，D-027）——与现行策略并行对比
    → data/store/sim/ledger_div.sqlite，日报 reports/div/

用法：
    python scripts/10_daily_run.py              # 完整每日流程（三个账户 + 邮件）
    python scripts/10_daily_run.py --no-push    # 不发送邮件
"""
import sys

import _bootstrap  # noqa: F401

from config import DEFAULT_START_DATE, STORE_DIR
from src.data import universe
from src.data.market import update_kline_batch
from src.data.storage import ParquetStore
from src.live.pipeline import run_pipeline


def main() -> None:
    import os

    push = "--no-push" not in sys.argv
    if "--skip-update" not in sys.argv:
        store = ParquetStore()
        symbols = universe.pit_union_symbols("000300", store)
        source = os.environ.get("QUANTDEX_SOURCE", "bs")
        allow_sina = os.environ.get("QUANTDEX_SINA_FALLBACK", "0") == "1"
        allow_bs = os.environ.get("QUANTDEX_BS_FALLBACK", "1") == "1"
        print(f">> 更新 PIT 历史成员行情（{len(symbols)} 只，增量，source={source}，"
              f"baostock兜底={allow_bs}）...")
        r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                               full_start=DEFAULT_START_DATE, source=source,
                               store=store, sina_fallback=allow_sina,
                               bs_fallback=allow_bs)
        failed = r.pop("__failed__", [])
        n_bs = sum(1 for v in r.values() if isinstance(v, str) and v.startswith("bs_fallback"))
        n_up = sum(1 for v in r.values() if v == "uptodate")
        print(f">> 行情更新完成：处理 {len(r)} 只（已最新 {n_up}、baostock兜底 {n_bs}），失败 {len(failed)} 只")
        for code, err in failed[:5]:
            print(f"   FAIL {code}: {err}")
        from src.data.snapshot import update_indexes
        print(">> 更新基准指数（沪深300 / 中证500）...")
        ir = update_indexes(store)
        print(f">> 指数完成：{len(ir)} 个")
    else:
        print(">> 跳过行情更新（--skip-update，使用本地已有数据）")

    print("")

    print("\n>> [账户 large] 影子盘流水线（100 万 / Top50 等权）...")
    run_pipeline(push=push)

    print("\n>> [账户 small] 影子盘流水线（2000 元 / 整手 ≤3 只）...")
    run_pipeline(capital=2000.0, push=push,
                 db_path=STORE_DIR / "sim" / "ledger_2000.sqlite",
                 label="small", max_positions=3)

    print("\n>> [账户 div] 影子盘流水线（100 万 / 红利版：低换手+低波+股息率）...")
    run_pipeline(push=push,
                 db_path=STORE_DIR / "sim" / "ledger_div.sqlite",
                 label="div", strategy="dividend")


if __name__ == "__main__":
    main()
