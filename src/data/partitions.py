# -*- coding: utf-8 -*-
"""
按日分区（append-only）存储层。

目的：解决"每次更新重写几百个股票文件 → git 里几百个二进制文件变化"的写放大。
布局：
    data/store/market/daily_by_date/YYYY-MM-DD.parquet   # 当天全部标的（提交 git）
    data/store/market/daily/{code}__{adjust}.parquet     # 本地读缓存（gitignore，可重建）

规则：
  - 更新只写"新增行"到当天分区；历史文件永不修改 → git 真正增量；
  - 股票文件是派生缓存，克隆后运行 scripts/18_rebuild_daily_cache.py 重建；
  - 分区行包含 adjust 列（qfq/hfq/none），保证重建时口径不混。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import STORE_DIR

PART_DIR = STORE_DIR / "market" / "daily_by_date"


def _with_adjust(df: pd.DataFrame, adjust: str) -> pd.DataFrame:
    out = df.copy()
    if "adjust" not in out.columns:
        out["adjust"] = adjust or "qfq"
    out["date"] = pd.to_datetime(out["date"])
    return out


def write_days(df: pd.DataFrame, adjust: str = "qfq") -> int:
    """把行写入按日分区（按 date 分组、按 symbol+adjust 去重）。返回写入的天数。"""
    if df is None or len(df) == 0:
        return 0
    PART_DIR.mkdir(parents=True, exist_ok=True)
    d = _with_adjust(df, adjust)
    n_days = 0
    for day, g in d.groupby(d["date"].dt.strftime("%Y-%m-%d")):
        path = PART_DIR / f"{day}.parquet"
        if path.exists():
            old = pd.read_parquet(path)
            old["date"] = pd.to_datetime(old["date"])
            merged = pd.concat([old, g], ignore_index=True)
        else:
            merged = g
        merged = (merged.drop_duplicates(subset=["symbol", "adjust"], keep="last")
                        .sort_values("symbol").reset_index(drop=True))
        merged.to_parquet(path, index=False, compression="snappy")
        n_days += 1
    return n_days


def queue_daily(store, df: pd.DataFrame, adjust: str = "qfq") -> None:
    """更新钩子：批量模式下缓冲，非批量立即写；只缓冲"新增行"（调用方传入 delta）。"""
    if df is None or len(df) == 0:
        return
    if getattr(store, "_reg_suspend", False):
        buf = getattr(store, "_daily_delta", None)
        if buf is None:
            store._daily_delta = []
            buf = store._daily_delta
        buf.append(_with_adjust(df, adjust))
    else:
        write_days(df, adjust)


def flush_queue(store) -> None:
    buf = getattr(store, "_daily_delta", None)
    if not buf:
        return
    df = pd.concat(buf, ignore_index=True)
    write_days(df)
    store._daily_delta = []


def migrate_from_symbols(store, verbose: bool = True) -> dict:
    """一次性迁移：933 个 per-symbol 文件 → 2132 个按日分区。"""
    names = [n for n in store.list_names("daily")]
    frames = []
    for i, name in enumerate(names, 1):
        try:
            df = store.read("daily", name)
        except Exception:  # noqa: BLE001
            continue
        adj = name.rsplit("__", 1)[-1] if "__" in name else "qfq"
        df = _with_adjust(df, adj)
        frames.append(df)
        if verbose and i % 200 == 0:
            print(f"   读取 {i}/{len(names)}", flush=True)
    if not frames:
        return {"days": 0, "rows": 0}
    allrows = pd.concat(frames, ignore_index=True)
    if verbose:
        print(f"   合并 {len(allrows)} 行，写分区...", flush=True)
    # 按日写（保留 adjust 列）
    PART_DIR.mkdir(parents=True, exist_ok=True)
    n_days = 0
    for day, g in allrows.groupby(allrows["date"].dt.strftime("%Y-%m-%d")):
        g = g.drop_duplicates(subset=["symbol", "adjust"], keep="last")
        g = g.sort_values("symbol").reset_index(drop=True)
        g.to_parquet(PART_DIR / f"{day}.parquet", index=False, compression="snappy")
        n_days += 1
    return {"days": n_days, "rows": int(len(allrows))}


def rebuild_symbol_cache(store, verbose: bool = True) -> dict:
    """从按日分区重建本地股票缓存（克隆后运行）。"""
    files = sorted(PART_DIR.glob("*.parquet"))
    if not files:
        return {"symbols": 0}
    frames = []
    for i, p in enumerate(files, 1):
        frames.append(pd.read_parquet(p))
        if verbose and i % 500 == 0:
            print(f"   读取分区 {i}/{len(files)}", flush=True)
    allrows = pd.concat(frames, ignore_index=True)
    n = 0
    for (sym, adj), g in allrows.groupby(["symbol", "adjust"]):
        g = g.sort_values("date").reset_index(drop=True)
        out = STORE_DIR / "market" / "daily" / f"{sym}__{adj}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        g.to_parquet(out, index=False, compression="snappy")
        n += 1
    return {"symbols": n, "rows": int(len(allrows))}
