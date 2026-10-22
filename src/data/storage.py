# -*- coding: utf-8 -*-
"""
Parquet 数据仓库：分区存储、读取、增量更新（upsert）、数据注册表。

数据集 kind 与目录、文件命名约定：
    daily      market/daily/{symbol}__{adjust}.parquet      （adjust: qfq/hfq/none）
    weekly     market/weekly/{symbol}__{adjust}.parquet
    monthly    market/monthly/{symbol}__{adjust}.parquet
    minute     market/minute/{symbol}__{freq}__{adjust}.parquet
    meta       meta/{name}.parquet
    valuation  valuation/{symbol}.parquet
    fin_abstract financials/abstract/{symbol}.parquet
    fin_bs     financials/baostock/{symbol}.parquet
    moneyflow  moneyflow/{name}.parquet
    macro      macro/{name}.parquet
"""
from __future__ import annotations

from contextlib import contextmanager

import pandas as pd

from config import (
    MARKET_DIR, META_DIR, VALUATION_DIR, FINANCIAL_DIR,
    MONEYFLOW_DIR, MACRO_DIR, REGISTRY_PATH, ensure_dirs,
)

_DIR_MAP = {
    "daily": MARKET_DIR / "daily",
    "weekly": MARKET_DIR / "weekly",
    "monthly": MARKET_DIR / "monthly",
    "minute": MARKET_DIR / "minute",
    "meta": META_DIR,
    "valuation": VALUATION_DIR,
    "fin_abstract": FINANCIAL_DIR / "abstract",
    "fin_bs": FINANCIAL_DIR / "baostock",
    "moneyflow": MONEYFLOW_DIR,
    "macro": MACRO_DIR,
}


class ParquetStore:
    """Parquet 数据仓库（单实例即可，无状态连接）。"""

    def __init__(self) -> None:
        ensure_dirs()
        # 进程内读缓存：{(路径, 列名): (mtime, df)}，写文件后按路径失效
        self._cache: dict = {}
        # 批量写注册表缓冲：batch_write 期间只更新内存，结束统一落盘一次
        self._reg_suspend = False
        self._reg_buf: dict = {}

    # -------------------------------------------------- 缓存
    def _invalidate_cache(self, path) -> None:
        sp = str(path)
        for key in [k for k in self._cache if k[0] == sp]:
            self._cache.pop(key, None)

    # -------------------------------------------------- 路径
    def _path(self, kind: str, name: str) -> "pd.io.common.PathLike":
        if kind not in _DIR_MAP:
            raise KeyError(f"未知数据集类型 kind={kind}，可选: {list(_DIR_MAP)}")
        base = _DIR_MAP[kind]
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{name}.parquet"

    # -------------------------------------------------- 基础读写
    def exists(self, kind: str, name: str) -> bool:
        return self._path(kind, name).exists()

    def write(self, df: pd.DataFrame, kind: str, name: str,
              source: str = "", date_col: str | None = None,
              adjust: str = "") -> None:
        """整表覆盖写入，并更新注册表。adjust 记录复权口径（仅行情类数据使用）。"""
        if df is None or len(df) == 0:
            return
        path = self._path(kind, name)
        df.to_parquet(path, index=False, compression="snappy")
        self._invalidate_cache(path)
        self._register(kind, name, len(df), source, date_col, df, adjust)

    def read(self, kind: str, name: str, columns: list[str] | None = None) -> pd.DataFrame:
        """读取数据集，不存在则抛 FileNotFoundError。带进程内缓存（按 mtime 失效）。"""
        path = self._path(kind, name)
        if not path.exists():
            raise FileNotFoundError(f"数据集不存在: {kind}/{name} ({path})")
        ckey = tuple(columns) if columns else None
        cache_key = (str(path), ckey)
        hit = self._cache.get(cache_key)
        if hit is not None and hit[0] == path.stat().st_mtime:
            return hit[1]
        df = pd.read_parquet(path, columns=columns)
        self._cache[cache_key] = (path.stat().st_mtime, df)
        return df

    def read_or_empty(self, kind: str, name: str) -> pd.DataFrame:
        """读取数据集，不存在返回空 DataFrame。"""
        return self.read(kind, name) if self.exists(kind, name) else pd.DataFrame()

    # -------------------------------------------------- 增量更新
    def upsert(self, df: pd.DataFrame, kind: str, name: str,
               keys: list[str], source: str = "",
               date_col: str | None = None, adjust: str = "") -> int:
        """
        增量合并：新数据按 keys 与旧数据去重（新数据优先），排序后写回。
        返回合并后的总行数。adjust 记录复权口径（仅行情类数据使用）。
        """
        if df is None or len(df) == 0:
            return 0
        if kind == "daily":
            try:
                from src.data.partitions import queue_daily
                queue_daily(self, df, adjust or "qfq")
            except Exception:  # noqa: BLE001
                pass
        if self.exists(kind, name):
            old = self.read(kind, name)
            merged = pd.concat([old, df], ignore_index=True)
        else:
            merged = df.copy()
        merged = merged.drop_duplicates(subset=keys, keep="last")
        merged = merged.sort_values(keys).reset_index(drop=True)
        self.write(merged, kind, name, source=source, date_col=date_col, adjust=adjust)
        return len(merged)

    # -------------------------------------------------- 注册表
    def _register(self, kind: str, name: str, rows: int,
                  source: str, date_col: str | None, df: pd.DataFrame,
                  adjust: str = "") -> None:
        """更新注册表中该数据集的元信息。"""
        min_date = max_date = pd.NaT
        if date_col and date_col in df.columns:
            d = pd.to_datetime(df[date_col], errors="coerce")
            min_date, max_date = d.min(), d.max()

        entry = pd.DataFrame([{
            "dataset": kind,
            "name": name,
            "rows": int(rows),
            "date_col": date_col or "",
            "min_date": min_date,
            "max_date": max_date,
            "source": source,
            "adjust": adjust,
            "updated_at": pd.Timestamp.now(),
        }])

        # 批量模式：只更新内存缓冲，由 batch_write 结束时统一落盘
        if self._reg_suspend:
            self._reg_buf[(kind, name)] = entry
            return

        self._write_registry_entry(kind, name, entry)

    @staticmethod
    def _merge_entry(reg: pd.DataFrame, kind: str, name: str,
                     entry: pd.DataFrame) -> pd.DataFrame:
        if reg.empty:
            return entry
        mask = (reg["dataset"] == kind) & (reg["name"] == name)
        return pd.concat([reg[~mask], entry], ignore_index=True)

    def _write_registry_entry(self, kind: str, name: str,
                              entry: pd.DataFrame) -> None:
        if REGISTRY_PATH.exists():
            reg = pd.read_parquet(REGISTRY_PATH)
            reg = self._merge_entry(reg, kind, name, entry)
        else:
            reg = entry
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        reg.to_parquet(REGISTRY_PATH, index=False)

    @contextmanager
    def batch_write(self):
        """批量写入上下文：期间注册表只在内存缓冲，退出时合并并只写盘一次。"""
        self._reg_suspend = True
        try:
            yield
        finally:
            self._reg_suspend = False
            self._flush_registry()
            try:
                from src.data.partitions import flush_queue
                flush_queue(self)
            except Exception:  # noqa: BLE001
                pass

    def _flush_registry(self) -> None:
        if not self._reg_buf:
            return
        reg = pd.read_parquet(REGISTRY_PATH) if REGISTRY_PATH.exists() else pd.DataFrame()
        for (kind, name), entry in self._reg_buf.items():
            reg = self._merge_entry(reg, kind, name, entry)
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        reg.to_parquet(REGISTRY_PATH, index=False)
        self._reg_buf.clear()

    def registry(self) -> pd.DataFrame:
        """返回数据注册表。"""
        if not REGISTRY_PATH.exists():
            return pd.DataFrame(columns=["dataset", "name", "rows", "date_col",
                                         "min_date", "max_date", "source",
                                         "adjust", "updated_at"])
        return pd.read_parquet(REGISTRY_PATH)

    def list_names(self, kind: str) -> list[str]:
        """列出某类数据集下所有 name（不含扩展名）。"""
        base = _DIR_MAP[kind]
        if not base.exists():
            return []
        return sorted(p.stem for p in base.glob("*.parquet"))
