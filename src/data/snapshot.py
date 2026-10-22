# -*- coding: utf-8 -*-
"""
全市场快照更新（一次请求拿全 A 股，替代 949 次逐只请求）。

用途：
  - 服务启动/点"更新行情"时，先尝试快照（约 1 次 HTTP，秒级）；
  - 快照失败（东财接口不可用）再回退到 baostock 逐只补齐（3~5 分钟）；
  - 只在收盘后（或非交易时段）把快照落库，避免把盘中价写成收盘价。

数据来源：akshare 东方财富实时行情（stock_zh_a_spot_em）。
"""
from __future__ import annotations

import time

import pandas as pd

from config import STORE_DIR
from src.data.storage import ParquetStore

_SPOT_COLS = {
    "代码": "symbol", "今开": "open", "最高": "high", "最低": "low",
    "最新价": "close", "成交量": "volume", "成交额": "amount",
    "振幅": "amplitude", "涨跌幅": "pct_chg", "涨跌额": "change",
    "换手率": "turnover",
}


def _trading_days(store: ParquetStore) -> pd.DatetimeIndex:
    cal = pd.read_parquet(STORE_DIR / "meta" / "trade_calendar.parquet")
    return pd.to_datetime(cal["date"]).sort_values().reset_index(drop=True)


def snapshot_date(store: ParquetStore) -> pd.Timestamp | None:
    """返回应落库的快照日期；盘中（<15:00）返回 None，避免写入未收盘价。"""
    now = pd.Timestamp.now()
    cal = _trading_days(store)
    today = now.normalize()
    if today in set(cal) and now.hour < 15:
        return None
    recent = cal[cal <= today]
    return recent.iloc[-1] if len(recent) else None


def fetch_spot() -> pd.DataFrame:
    from src.data.sources import ak
    df = ak.stock_zh_a_spot_em()
    if df is None or len(df) == 0:
        raise RuntimeError("快照为空（东财接口无数据）")
    return df


def update_spot_snapshot(store: ParquetStore | None = None,
                         verbose: bool = True) -> dict:
    """一次请求 → 更新所有 PIT 成员的当日快照；返回统计。"""
    store = store or ParquetStore()
    d0 = snapshot_date(store)
    if d0 is None:
        return {"ok": False, "reason": "盘中或非收盘时段，跳过落库"}
    t0 = time.time()
    df = fetch_spot()
    d = df[list(_SPOT_COLS)].rename(columns=_SPOT_COLS).copy()
    d["symbol"] = d["symbol"].astype(str).str.zfill(6)
    d["date"] = d0
    for c in ("open", "high", "low", "close", "amount", "amplitude",
              "pct_chg", "change", "turnover"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    # akshare 成交量单位=手，仓库统一为股
    d["volume"] = pd.to_numeric(d["volume"], errors="coerce") * 100.0
    d["source"] = "akshare/spot_em"
    d = d.dropna(subset=["close"])

    names = set(store.list_names("daily"))
    valid = {n.split("__")[0] for n in names if n.endswith("__qfq")}
    d = d[d["symbol"].isin(valid)]
    if verbose:
        print(f">> 快照：{len(d)} 只（{d0.date()}），开始写入...", flush=True)

    updated, failed = 0, 0
    cols = ["date", "symbol", "open", "high", "low", "close", "volume",
            "amount", "amplitude", "pct_chg", "change", "turnover", "source"]
    with store.batch_write():
        for r in d.itertuples(index=False):
            sym = str(r.symbol)
            try:
                old = store.read("daily", f"{sym}__qfq")
                old = old[pd.to_datetime(old["date"]) != d0]
                row = pd.DataFrame([{c: getattr(r, c) for c in cols}])
                new = pd.concat([old[cols], row], ignore_index=True)
                new = new.sort_values("date").reset_index(drop=True)
                store.write(new, "daily", f"{sym}__qfq",
                            source="akshare/spot_em", date_col="date")
                updated += 1
            except Exception:  # noqa: BLE001
                failed += 1
    ms = int((time.time() - t0) * 1000)
    if verbose:
        print(f">> 快照完成：更新 {updated} 只，失败 {failed}，耗时 {ms/1000:.0f} 秒", flush=True)
    return {"ok": updated > 0, "date": str(d0.date()), "updated": updated,
            "failed": failed, "ms": ms}


def update_indexes(store: ParquetStore | None = None) -> dict:
    """更新基准指数（沪深300/中证500）——它们不在股票池里，必须单独更新。"""
    from src.data.market import update_kline_batch
    store = store or ParquetStore()
    r = update_kline_batch(["000300", "000905"], asset="index", adjust="none",
                           source="bs", store=store)
    r.pop("__failed__", None)
    return r


def freshness(store: ParquetStore | None = None, sample: int = 30) -> dict:
    """数据新鲜度：最近收盘交易日 vs 抽样文件的最新日期。"""
    store = store or ParquetStore()
    cal = _trading_days(store)
    now = pd.Timestamp.now()
    today = now.normalize()
    if today in set(cal) and now.hour < 15:
        closed = cal[cal < today].iloc[-1]   # 当日尚未收盘
    else:
        closed = cal[cal <= today].iloc[-1]
    names = [n for n in store.list_names("daily") if n.endswith("__qfq")][:sample]
    have = 0
    for n in names:
        try:
            d = store.read("daily", n)
            if pd.to_datetime(d["date"]).max() >= closed:
                have += 1
        except Exception:  # noqa: BLE001
            pass
    return {"last_closed": str(closed.date()), "sample": len(names),
            "up_to_date": have,
            "stale": have < max(1, int(len(names) * 0.8))}
