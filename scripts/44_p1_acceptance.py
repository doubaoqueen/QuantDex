"""P1 数据补全验收（44 号）：估值/财务/摘要三层落盘核验（2026-11-14，P1-b 首轮教训产物）。

验收原则（教训②）：错误计数必须来自落盘核验，不来自日志或异常捕获。
用法：python scripts/44_p1_acceptance.py    # 03 号批量跑完后执行
"""
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from config import FINANCIAL_DIR
from src.data.storage import ParquetStore

BS_COMPLETE_MIN = 8          # 六件套6 + 分红1 + 复权因子1
TARGET = 949


def main() -> None:
    store = ParquetStore()
    print(f"验收目标：{TARGET} 只（PIT 并集）\n")

    # ---- P1-a 估值（valuation kind） ----
    val = store.list_names("valuation")
    n_val = sum(1 for s in val if not s.startswith("index_"))
    print(f"[P1-a] 估值：{n_val}/{TARGET} 只"
          f"（{'✅ 达标' if n_val >= TARGET - 30 else '⚠️ 未达标'}，指数估值表另计）")

    # ---- fin_abstract 摘要 ----
    abs_dir = FINANCIAL_DIR / "abstract"
    n_abs = len(list(abs_dir.glob("*.parquet"))) if abs_dir.exists() else 0
    print(f"[摘要] fin_abstract：{n_abs}/{TARGET} 只（部分即有价值，供基本面探索）")

    # ---- P1-b 财务六件套（fin_bs 件数完整性 = 断点锚点） ----
    bs_dir = FINANCIAL_DIR / "baostock"
    by_sym = Counter()
    for f in bs_dir.glob("*.parquet"):
        by_sym[f.stem.split("__")[0]] += 1
    complete = [s for s, v in by_sym.items() if v >= BS_COMPLETE_MIN]
    partial = [s for s, v in by_sym.items() if 0 < v < BS_COMPLETE_MIN]
    none = TARGET - len(by_sym)
    print(f"[P1-b] 财务六件套：完整 {len(complete)} | 部分 {len(partial)} | 无 {none}")
    dist = Counter(by_sym.values())
    print(f"    件数分布：{dict(sorted(dist.items()))}")

    # ---- 抽样质检：完整标的中抽 1 只看六件套行数与日期覆盖 ----
    if complete:
        sample = sorted(complete)[0]
        for f in sorted(bs_dir.glob(f"{sample}__*.parquet")):
            df = pd.read_parquet(f)
            print(f"    抽检 {f.stem}: {len(df)} 行")
        print(f"    （抽样标的 {sample}）")

    # ---- 结论 ----
    print("\n结论：")
    if n_val >= TARGET - 30:
        print("  P1-a 估值 ✅ 完成——市值中性化换真值、估值因子解锁")
    if len(complete) >= TARGET - 30:
        print("  P1-b 财务六件套 ✅ 完成——基本面因子族解锁（P1-c 资金流为下一块）")
    elif len(complete) > 0:
        print(f"  P1-b 财务六件套 ⏳ 部分（{len(complete)}/{TARGET}）——重跑 03 号自动补漏"
              f"（断点锚点=fin_bs 件数，冷却后执行；bs 限流时勿硬冲）")
    else:
        print("  P1-b 财务六件套 ❌ 未落地——检查 bs 连通性（net_diag）后冷却重跑")


if __name__ == "__main__":
    main()
