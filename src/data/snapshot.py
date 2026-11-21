# -*- coding: utf-8 -*-
"""
全市场快照更新（一次请求拿全 A 股，替代 949 次逐只请求）。

用途：
  - 服务启动/点"更新行情"时，先尝试快照（约 1 次 HTTP，秒级）；
  - 快照失败（东财接口不可用）再回退到 baostock 逐只补齐（3~5 分钟）；
  - 只在收盘后（或非交易时段）把快照落库，避免把盘中价写成收盘价；
  - 开盘前（00:00~09:15）落库日期回退到**上一个交易日**——凌晨补跑能补回昨天（D-080）。

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


def _trading_days(store: ParquetStore | None = None) -> pd.DatetimeIndex:
    cal = pd.read_parquet(STORE_DIR / "meta" / "trade_calendar.parquet")
    return pd.DatetimeIndex(pd.to_datetime(cal["date"])).sort_values()


# 交易时段边界（D-080）。集合竞价 09:15 起东财快照就开始出现"当日"竞价价，
# 15:00 收盘。开盘前（00:00~09:15）快照仍是**上一个交易日**的收盘价——这正是
# "凌晨更新拉不到昨天行情"的根因：旧代码只要"今天是交易日且 hour<15"就判盘中，
# 凌晨直接跳过，于是漏跑那天的行情永远补不上。
AUCTION_AT = (9, 15)
CLOSE_AT = (15, 0)


def _session(now: pd.Timestamp, cal: pd.DatetimeIndex) -> tuple[bool, pd.Timestamp | None]:
    """返回 (是否盘中, 最近已收盘交易日)。

    盘中 = 交易日 09:15~15:00（快照是未收盘价，不能落库）；
    交易日开盘前 → 上一个交易日（快照仍是它的收盘价）；
    交易日收盘后 / 非交易日 → 最近一个 <= 今天的交易日。
    """
    today = now.normalize()
    hm = (now.hour, now.minute)
    is_trading = today in set(cal)
    if is_trading and hm >= CLOSE_AT:
        return False, today
    intraday = is_trading and AUCTION_AT <= hm < CLOSE_AT
    before = cal[cal < today] if is_trading else cal[cal <= today]
    # 用 max() 而不是 .iloc[-1]：cal 可能是 DatetimeIndex 也可能被外部传成 Series
    return intraday, (before.max() if len(before) else None)


def snapshot_date(store: ParquetStore | None = None,
                  now: pd.Timestamp | None = None) -> pd.Timestamp | None:
    """返回应落库的快照日期；盘中返回 None，避免写入未收盘价。

    D-080：旧实现把"开盘前"也算盘中（`hour < 15`），凌晨/清早跑直接跳过，
    19:00 那次没跑的话，那天的行情就再也补不上了。现在区分三段：
      - 交易日 09:15~15:00（含集合竞价）→ None；
      - 交易日 00:00~09:15 → 上一个交易日（快照仍是它的收盘价）；
      - 收盘后 / 非交易日 → 最近一个 <= 今天的交易日。
    """
    store = store or ParquetStore()
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    intraday, closed = _session(now, _trading_days(store))
    return None if intraday else closed


# 快照重试参数：间隔 45s（服务端限流/抖动的恢复窗口），不用 retry_call 的 1.5/3s
# 短退避——东财断连后立即重试大概率还是断（2026-11-13）。
_SPOT_RETRY_ATTEMPTS = 3
_SPOT_RETRY_DELAY_S = 45.0


def fetch_spot(max_attempts: int | None = None,
               delay_s: float | None = None) -> pd.DataFrame:
    """东财全市场快照（单次 HTTP），带**长间隔**重试。

    教训：这里曾是无保护裸调——一次 RemoteDisconnected 就让自动链转 baostock
    全量兜底（949 只逐只 + 分钟线，10~20 分钟）。实测东财通常可用，失败多为
    瞬时断连；长间隔重试 2 次即可挡住绝大多数抖动（market.py 同接口本就有
    retry_call 保护，唯独快照漏了）。重试仍失败才抛给调用方的兜底路径。
    """
    attempts = max_attempts or _SPOT_RETRY_ATTEMPTS
    delay = _SPOT_RETRY_DELAY_S if delay_s is None else delay_s
    from src.data.sources import ak
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            df = ak.stock_zh_a_spot_em()
            if df is None or len(df) == 0:
                raise RuntimeError("快照为空（东财接口无数据）")
            return df
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if attempt < attempts:
                print(f"[spot] 东财快照第 {attempt}/{attempts} 次失败（{str(e)[:80]}），"
                      f"{delay:.0f}s 后重试", flush=True)
                time.sleep(delay)
    raise RuntimeError(f"东财快照重试 {attempts} 次仍失败") from last_exc


def _check_session_payload(d: pd.DataFrame) -> None:
    """落库前校验"拿到的是一整段真实交易"的报价，而不是开盘前的空壳（D-080）。

    风险场景：凌晨把快照回填到上一个交易日时，若东财已经重置为下一交易日
    （今开/最高/最低=0、成交量=0），写进去就是一条 open=0 的假 K 线——比不写更糟。
    """
    if not len(d):
        raise RuntimeError("快照无有效行")
    open_ok = (pd.to_numeric(d["open"], errors="coerce").fillna(0) > 0).mean()
    vol_sum = float(pd.to_numeric(d["volume"], errors="coerce").fillna(0).sum())
    if open_ok < 0.5 or vol_sum <= 0:
        raise RuntimeError(
            f"快照不像真实交易日（有开盘价 {open_ok:.0%}、成交量合计 {vol_sum:,.0f}），"
            f"疑似未开盘已重置，不落库")


def update_spot_snapshot(store: ParquetStore | None = None,
                         verbose: bool = True) -> dict:
    """一次请求 → 更新所有 PIT 成员的当日快照；返回统计。"""
    store = store or ParquetStore()
    d0 = snapshot_date(store)
    if d0 is None:
        return {"ok": False, "kind": "intraday",
                "reason": "交易日盘中（09:15~15:00），跳过落库"}
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
    _check_session_payload(d)

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
                row = pd.DataFrame([{c: getattr(r, c) for c in cols}])
                # upsert 只传新增行：内部合并到股票缓存，并把 delta 送到按日分区
                store.upsert(row, "daily", f"{sym}__qfq", keys=["symbol", "date"],
                             source="akshare/spot_em", date_col="date",
                             adjust="qfq")
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


def freshness(store: ParquetStore | None = None, sample: int = 30,
              now: pd.Timestamp | None = None) -> dict:
    """数据新鲜度：最近收盘交易日 vs 抽样文件的最新日期。"""
    store = store or ParquetStore()
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    _, closed = _session(now, _trading_days(store))
    if closed is None:
        return {"last_closed": None, "sample": 0, "up_to_date": 0, "stale": True,
                "error": "交易日历缺失"}
    names = [n for n in store.list_names("daily") if n.endswith("__qfq")]
    # 深检修复（2026-11-22）：原 [:sample] 取字母序前 30 个（永远偏 000/600 前缀，
    # 不代表全池），改为等距抽样覆盖整个股票池。
    step = max(1, len(names) // max(1, sample))
    names = names[::step][:sample]
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


def _partition_rows(day: pd.Timestamp) -> int:
    """某交易日的按日分区行数（没有分区=0）。"""
    p = STORE_DIR / "market" / "daily_by_date" / f"{day.date()}.parquet"
    if not p.exists():
        return 0
    try:
        return int(len(pd.read_parquet(p, columns=["symbol"])))
    except Exception:  # noqa: BLE001  坏分区按缺失处理（会被重抓覆盖）
        return 0


def missing_trade_days(store: ParquetStore | None = None, lookback: int = 30,
                       now: pd.Timestamp | None = None) -> list[str]:
    """最近 lookback 个已收盘交易日里，**整天缺失**的日期（升序 YYYY-MM-DD）。

    为什么必须单独检测（D-080）：
      - 东财快照只能写"最近一个收盘日"那一天，中间漏掉的日子它永远补不了；
      - 增量更新（update_kline_batch）只从"本地最后日期+1"抓起，一旦快照把序列
        推到了今天，中间那个洞就再也不会被请求——数据静悄悄永久缺失；
      - freshness 只比"最新日期"，中间的洞看不见（11-10 有数据即判 up_to_date）。

    判定基准是按日分区（append-only、按天写，见 partitions.py）：那天没跑更新 →
    没有分区；只写了指数（2026-11-09 当时只有 2 行）→ 行数远低于邻日 → 同样算缺失。
    只扫最近 lookback 个交易日：更早的洞靠回退起抓日已不经济，需要时用
    18_rebuild_daily_cache + 全史重抓处理。

    **只看 `< closed`，不含"最近收盘日"本身**（D-083）：收盘日还没抓下来属于
    "该更新了"（freshness 的 stale 管这一段，走快照/正常增量）；把它当洞会致命——
    15:00~16:00 窗口里历史源（16:00 才认当天已收盘）还拿不到当天数据，起抓日会比
    上界还大，整批 949 只逐个报"起始日期大于终止日期"（2026-11-16 15:04 实测）。
    真正的"漏跑一整天"永远发生在最近收盘日**之前**，这个窗口靠 `closed` 天然覆盖。
    """
    store = store or ParquetStore()
    now = pd.Timestamp.now() if now is None else pd.Timestamp(now)
    cal = _trading_days(store)
    _, closed = _session(now, cal)
    if closed is None:
        return []
    window = cal[cal < closed]
    window = window[-max(1, int(lookback)):]
    if not len(window):
        return []
    counts = {d: _partition_rows(d) for d in window}
    typical = pd.Series([v for v in counts.values() if v > 0])
    # 阈值取"有数据的邻日中位数的一半"：既能识别完全没有分区，也能识别只写了
    # 指数/几只票的半截分区；分区全都为空时退化为 1（全部视为缺失 → 触发整段重抓）
    threshold = max(1, int(0.5 * float(typical.median()))) if len(typical) else 1
    return [str(d.date()) for d in window if counts[d] < threshold]
