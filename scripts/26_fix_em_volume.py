"""一次性修复：东财日线 akshare/em 行成交量单位（手 → 股，D-059）。

背景：standardize_ak_daily 早先把东财"成交量"（单位=手）直接入库，与仓库
"volume=股"约定差 100 倍；snapshot 快照路径（akshare/spot_em）与 baostock/新浪
路径都正确，唯独 em 日线路径漏换算。实测 111 个 qfq 文件 279 行受污染
（隐含 VWAP = amount/volume 高出正常值 100 倍），量类因子/OBV 会被污染。

修复：对 daily / daily_by_date / weekly / monthly 中 source=='akshare/em'
且仍为"手"口径的行 volume ×100。检测用 schema.em_volume_in_lots（幂等：
已修复行不满足条件，重跑不会二次放大）。minute 目录为空，一并扫描以防万一。

用法：python scripts/26_fix_em_volume.py [--dry-run]
"""
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.schema import em_volume_in_lots

_MARKET = STORE_DIR / "market"


def fix_file(path: Path, dry_run: bool) -> tuple[int, int]:
    """修复单个 parquet，返回 (修复行数, em 总行数)。无 em 行的文件不动。"""
    df = pd.read_parquet(path)
    if "source" not in df.columns or len(df) == 0:
        return 0, 0
    em = df["source"] == "akshare/em"
    if not em.any():
        return 0, 0
    mask = em & em_volume_in_lots(df)
    skipped = int(em.sum() - mask.sum())  # em 行但检测不通过（理论上应为 0）
    if skipped:
        print(f"  [warn] {path.name}: {skipped} 行 em 行未通过手数检测（保持不动，请人工核对）")
    if mask.any() and not dry_run:
        df.loc[mask, "volume"] = pd.to_numeric(df.loc[mask, "volume"]) * 100.0
        df.to_parquet(path, index=False, compression="snappy")
    return int(mask.sum()), int(em.sum())


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    total_fixed = total_em = files_touched = 0
    # per-symbol 缓存（daily/weekly/monthly/minute）与按日分区真源都要修，
    # 两处是同一批数据的两份拷贝（D-015），只修一边会造成缓存/真源分叉。
    targets: list[Path] = []
    for sub in ("daily", "weekly", "monthly", "minute", "daily_by_date"):
        d = _MARKET / sub
        if d.exists():
            targets.extend(sorted(d.glob("*.parquet")))
    print(f"扫描 {len(targets)} 个文件（--dry-run 只报告不写入）")
    for p in targets:
        try:
            fixed, em_n = fix_file(p, dry_run)
        except Exception as e:  # noqa: BLE001
            print(f"  [error] {p.name}: {e}")
            continue
        total_em += em_n
        if fixed:
            total_fixed += fixed
            files_touched += 1
            print(f"  {p.parent.name}/{p.name}: 修复 {fixed} 行")
    verb = "将修复" if dry_run else "已修复"
    print(f"\n{verb}: {total_fixed} 行 / {files_touched} 个文件（em 行总数 {total_em}）")
    if not dry_run and total_fixed == 0:
        print("无需修复（可能已修复过）——幂等重跑安全。")


if __name__ == "__main__":
    main()
