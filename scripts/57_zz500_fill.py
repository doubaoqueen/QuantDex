"""第 57 步：中证500 独有标的行情补全（D-096/D-101 中证500 零覆盖的修复）。

背景：905 PIT 并集 1000 只，独有 514 只**全部无行情**（D-096/D-101 实测）——
"沪深300+中证500 并集"的表述因此失实。本脚本用 baostock 补齐这 514 只的
2018+ 日线（qfq），完成后：
  - "300+500 并集"表述成立（PERFORMANCE.md 修正生效）
  - 后续研究（挖矿/中性化/小盘池）可覆盖中证500 独有成分
  - 下一步可扩 905 PIT 的分钟线与分红（另排）

用法：python scripts/57_zz500_fill.py            # 全量补齐（约 25-40 分钟，baostock）
     python scripts/57_zz500_fill.py 50         # 只补前 50 只（试跑）
"""
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.market import update_kline_batch
from src.data.storage import ParquetStore


def main() -> None:
    store = ParquetStore()
    p3 = set(pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000300.parquet")["symbol"].astype(str))
    p9 = set(pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000905.parquet")["symbol"].astype(str))
    only500 = sorted(p9 - p3)
    todo = [c for c in only500 if not store.exists("daily", f"{c}__qfq")]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    if limit:
        todo = todo[:limit]
    print(f">> 中证500 独有标的：并集内 {len(only500)} 只，无行情 {len(todo)} 只待补", flush=True)
    if not todo:
        print(">> 无需补齐（全部已有行情）")
        return

    r = update_kline_batch(todo, asset="stock", adjust="qfq",
                           full_start="20180101", source="bs", store=store,
                           sina_fallback=False, bs_fallback=False)
    failed = r.pop("__failed__", [])
    n_up = sum(1 for v in r.values() if v == "uptodate")
    print(f">> 完成：处理 {len(r)} 只（已最新 {n_up}），失败 {len(failed)} 只", flush=True)
    for item in failed[:10]:
        print(f"   FAIL {item}", flush=True)
    if failed:
        print(">> 有失败——重跑本脚本自动补漏（断点续传）", flush=True)
        sys.exit(1)
    print(">> 中证500 独有标的行情补齐 ✓——'300+500 并集'表述自此成立（PERFORMANCE.md 已修正）")


if __name__ == "__main__":
    main()
