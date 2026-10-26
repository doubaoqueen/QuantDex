"""
P0 参考数据补全（docs/DATA_ROADMAP.md）：
  1) 全量证券表（含退市）：meta/bs_securities —— 退市研究 + 无偏股票池底座
  2) 行业分类（东财板块）：meta/industry_em —— 替代本机挂起的 baostock 行业接口，
     解锁行业中性化（只覆盖当前上市，退市股无行业）
  3) 复权因子全量：financials/baostock/{sym}__adjfactor —— PIT 并集逐只，
     已存在即跳过（断点续跑），供未复权口径/除权分析

用法：
    python scripts/20_fetch_reference_data.py            # 全部三项
    python scripts/20_fetch_reference_data.py securities  # 只跑第 1 项
    python scripts/20_fetch_reference_data.py industry    # 只跑第 2 项
    python scripts/20_fetch_reference_data.py adjfactor   # 只跑第 3 项
"""
import sys

import _bootstrap  # noqa: F401

from src.data import financials, universe
from src.data.storage import ParquetStore


def do_securities(store: ParquetStore) -> None:
    print(">> [1/3] 全量证券表（baostock，含 IPO/退市日期）...", flush=True)
    df = universe.fetch_bs_securities(store)
    n_out = int((df["out_date"].astype(str).str.strip() != "").sum())
    print(f"   共 {len(df)} 条（其中已退市 {n_out} 条） -> meta/bs_securities", flush=True)


def do_industry(store: ParquetStore) -> None:
    print(">> [2/3] 行业分类（申万一级，替代挂起/被拒的 baostock 与东财接口）...", flush=True)
    df = universe.fetch_industry_sw(store)
    print(f"   共 {len(df)} 只 / {df['industry'].nunique()} 个一级行业 -> meta/industry_sw", flush=True)


def do_adjfactor(store: ParquetStore, limit: int = 0) -> None:
    symbols = universe.pit_union_symbols("000300", store)
    if limit:
        symbols = symbols[:limit]
    todo = [s for s in symbols
            if not store.exists("fin_bs", f"{s}__adjfactor")]
    print(f">> [3/3] 复权因子：PIT 并集 {len(symbols)} 只，待抓 {len(todo)} 只"
          f"（已有 {len(symbols) - len(todo)}，断点续跑）", flush=True)
    failed: list[tuple[str, str]] = []
    with store.batch_write():
        for i, s in enumerate(todo, 1):
            try:
                df = financials.fetch_adjust_factor(s, start="2005-01-01", store=store)
                _ = len(df)
            except Exception as e:  # noqa: BLE001
                failed.append((s, str(e)[:60]))
            if i % 50 == 0 or i == len(todo):
                print(f"   进度 {i}/{len(todo)}，失败 {len(failed)}", flush=True)
    print(f"   完成：失败 {len(failed)} 只（多为 2008 前退市/无除权记录）", flush=True)
    for code, err in failed[:10]:
        print(f"   FAIL {code}: {err}")


def main() -> None:
    store = ParquetStore()
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "securities"):
        do_securities(store)
    if which in ("all", "industry"):
        do_industry(store)
    if which in ("all", "adjfactor"):
        do_adjfactor(store)
    print(">> 完成。")


if __name__ == "__main__":
    main()
