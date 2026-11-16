"""D-080 回归：快照落库日期口径 + "整天缺失"交易日检测 + 增量补洞起抓日。

背景（2026-11-10 用户反馈）：设计是每天 19:00 手动跑一次更新；若错过，凌晨
（次日开盘前）补跑时旧代码把"今天是交易日且 hour<15"一律判成盘中 → 直接跳过，
昨天（11-09）的行情永远拉不到；再往后跑又会因"本地已最新"把中间那天永久跳过。
"""
import shutil
from pathlib import Path

import pandas as pd

from src.data import market, snapshot, storage

# 2026-11-02(一) ~ 11-11(三)，含周末空档
CAL = pd.DatetimeIndex(sorted({
    "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
    "2026-11-09", "2026-11-10", "2026-11-11",
}))

WORK = Path("data/store/_test_snapshot_dates")   # 工作区内造临时仓库（系统 temp 可能不可写）


def _session(ts):
    return snapshot._session(pd.Timestamp(ts), CAL)


def _snapshot_date(ts):
    """用固定日历替换真实交易日历，只验证日期口径。"""
    orig = snapshot._trading_days
    snapshot._trading_days = lambda store=None: CAL
    try:
        return snapshot.snapshot_date(now=pd.Timestamp(ts))
    finally:
        snapshot._trading_days = orig


def _missing_days(partitions: dict, at: str = "2026-11-10 01:41"):
    """用假仓库根目录（日历 + 按日分区）跑缺失检测。partitions: {日期: 分区行数}。"""
    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "meta").mkdir(parents=True)
    (WORK / "market" / "daily_by_date").mkdir(parents=True)
    pd.DataFrame({"date": CAL}).to_parquet(WORK / "meta" / "trade_calendar.parquet")
    for day, rows in partitions.items():
        pd.DataFrame({"symbol": [f"{i:06d}" for i in range(rows)],
                      "date": pd.Timestamp(day)}).to_parquet(
            WORK / "market" / "daily_by_date" / f"{day}.parquet")
    orig = snapshot.STORE_DIR
    snapshot.STORE_DIR = WORK
    try:
        return snapshot.missing_trade_days(now=pd.Timestamp(at))
    finally:
        snapshot.STORE_DIR = orig
        shutil.rmtree(WORK, ignore_errors=True)


def test_preopen_uses_previous_trading_day():
    """开盘前（00:00~09:15）落库日期 = 上一个交易日，而不是被当成盘中跳过。

    这正是用户踩到的 bug：11-10 凌晨更新，旧代码返回 None（盘中），11-09 永远补不上。
    """
    for hm in ("00:05", "01:41", "07:59", "09:14"):
        intraday, closed = _session(f"2026-11-10 {hm}")
        assert intraday is False, hm
        assert closed == pd.Timestamp("2026-11-09"), (hm, closed)
        assert _snapshot_date(f"2026-11-10 {hm}") == pd.Timestamp("2026-11-09")


def test_intraday_is_skipped():
    """交易日 09:15~15:00（含集合竞价）不落库：快照是未收盘价。"""
    for hm in ("09:15", "09:30", "11:30", "14:59"):
        intraday, closed = _session(f"2026-11-10 {hm}")
        assert intraday is True, hm
        assert closed == pd.Timestamp("2026-11-09"), (hm, closed)
        assert _snapshot_date(f"2026-11-10 {hm}") is None


def test_after_close_and_non_trading_day():
    """收盘后 → 当天；周末/节假日 → 最近一个交易日。"""
    assert _snapshot_date("2026-11-10 15:00") == pd.Timestamp("2026-11-10")
    assert _snapshot_date("2026-11-10 19:00") == pd.Timestamp("2026-11-10")
    # 周六凌晨（11-07/11-08 都不是交易日）→ 11-06 周五
    assert _snapshot_date("2026-11-08 01:30") == pd.Timestamp("2026-11-06")
    assert _snapshot_date("2026-11-08 19:00") == pd.Timestamp("2026-11-06")


def test_freshness_agrees_with_snapshot_date():
    """freshness 与实际落库日期必须同源，否则自动链会反复空转。"""
    orig = snapshot._trading_days
    snapshot._trading_days = lambda store=None: CAL
    try:
        f = snapshot.freshness(sample=0, now=pd.Timestamp("2026-11-10 01:41"))
    finally:
        snapshot._trading_days = orig
    assert f["last_closed"] == "2026-11-09"


def test_missing_trade_days_finds_whole_day_and_partial_partition():
    """整天没跑更新 → 无分区；只写了指数 → 行数远低于邻日；两者都必须被识别。

    取收盘后（20:00）的时点：D-083 起"最近收盘日"本身不算洞（属于"该更新了"），
    所以要在它之**前**的日子上验证识别能力。
    """
    parts = {str(d.date()): 20 for d in CAL}
    del parts["2026-11-04"]            # 整天空缺：连分区都没有
    parts["2026-11-09"] = 2            # 只写了两条指数（11-09 的真实状态）
    assert _missing_days(parts, at="2026-11-10 20:00") == ["2026-11-04", "2026-11-09"]


def test_missing_trade_days_empty_when_complete():
    parts = {str(d.date()): 10 for d in CAL}
    assert _missing_days(parts) == []


def test_incremental_start_fills_hole_not_next_day():
    """增量起抓日：有洞时回退到洞的第一天——纯"最后日期+1"会把洞永久跳过。"""
    ts = pd.Timestamp
    # 已最新且无洞 → 不发请求
    assert market._incremental_start(ts("2026-11-09"), ts("2026-11-09"), None, "20150101") is None
    # 快照已把序列推到 11-10，但 11-09 整天缺失 → 必须从 11-09 重抓（不是 11-11）
    assert market._incremental_start(ts("2026-11-10"), ts("2026-11-10"),
                                     "2026-11-09", "20150101") == "20261109"
    # 落后于最近收盘日 → 从本地最后日期+1
    assert market._incremental_start(ts("2026-11-06"), ts("2026-11-10"),
                                     None, "20150101") == "20261107"
    # 无本地数据 → 全史
    assert market._incremental_start(None, ts("2026-11-10"), None, "20150101") == "20150101"


def test_missing_trade_days_ignores_latest_closed_day():
    """15:00~16:00 的"今天还没抓"不是洞（D-083）。

    历史源的"最近收盘日"要 16:00 才认当天，此刻把最近收盘日当缺失会让起抓日越过
    上界 → 949 只逐个报"起始日期大于终止日期"（2026-11-16 15:04 实测，整批白跑）。
    """
    parts = {str(d.date()): 20 for d in CAL if str(d.date()) != "2026-11-11"}
    assert _missing_days(parts, at="2026-11-11 15:12") == []
    # 但"最近收盘日之前"的真洞仍必须报出来
    parts2 = {str(d.date()): 20 for d in CAL
              if str(d.date()) not in ("2026-11-10", "2026-11-11")}
    assert _missing_days(parts2, at="2026-11-11 15:12") == ["2026-11-10"]


def test_spot_payload_guard_rejects_preopen_reset():
    """开盘前若东财已重置（今开/成交量全 0），绝不能写成上一个交易日的 K 线。"""
    good = pd.DataFrame({"open": [10.0, 11.0], "volume": [1000.0, 2000.0]})
    snapshot._check_session_payload(good)          # 不抛异常
    bad = pd.DataFrame({"open": [0.0, 0.0], "volume": [0.0, 0.0]})
    try:
        snapshot._check_session_payload(bad)
    except RuntimeError as e:
        assert "重置" in str(e)
    else:
        raise AssertionError("开盘前重置的空壳快照必须被拒绝")


def test_update_kline_batch_requests_from_hole():
    """集成：本地缺 11-09（快照已把序列推到 11-10）时，批量增量必须从 11-09
    起重抓，而不是因"本地已最新"直接跳过；抓取上界固定为最近收盘日。"""
    from src.data.storage import ParquetStore

    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "meta").mkdir(parents=True)
    (WORK / "market" / "daily_by_date").mkdir(parents=True)
    (WORK / "daily").mkdir(parents=True)
    pd.DataFrame({"date": CAL}).to_parquet(WORK / "meta" / "trade_calendar.parquet")
    # 本地序列：11-09 那天缺失，但 11-10 已有（正是"快照补了最新、中间留洞"）
    have = [d for d in CAL if str(d.date()) != "2026-11-09"]
    pd.DataFrame({"date": have, "symbol": "600519"}).to_parquet(
        WORK / "daily" / "600519__qfq.parquet")
    for d in CAL:
        if str(d.date()) == "2026-11-09":
            continue
        pd.DataFrame({"symbol": ["600519"], "date": d}).to_parquet(
            WORK / "market" / "daily_by_date" / f"{d.date()}.parquet")

    calls = []
    orig_map = dict(storage._DIR_MAP)
    orig_dir, orig_fetch, orig_lc = snapshot.STORE_DIR, market.fetch_kline, market.last_closed_trade_date
    try:
        storage._DIR_MAP["daily"] = WORK / "daily"
        storage._DIR_MAP["meta"] = WORK / "meta"
        snapshot.STORE_DIR = WORK
        snapshot._trading_days = lambda store=None: CAL
        market.last_closed_trade_date = lambda store=None: pd.Timestamp("2026-11-10")
        market.fetch_kline = lambda code, **kw: (
            calls.append((kw.get("start"), kw.get("end"))), pd.DataFrame())[1]
        market.update_kline_batch(["600519"], store=ParquetStore(), source="bs")
    finally:
        storage._DIR_MAP.clear()
        storage._DIR_MAP.update(orig_map)
        snapshot.STORE_DIR = orig_dir
        snapshot._trading_days = lambda store=None: CAL
        market.fetch_kline, market.last_closed_trade_date = orig_fetch, orig_lc
        shutil.rmtree(WORK, ignore_errors=True)

    assert calls == [("20261109", "20261110")], calls


def test_update_kline_batch_skips_when_truly_current():
    """无洞且已最新 → 一个请求都不发（盘后/周末重跑仍应是秒级）。"""
    from src.data.storage import ParquetStore

    shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "meta").mkdir(parents=True)
    (WORK / "market" / "daily_by_date").mkdir(parents=True)
    (WORK / "daily").mkdir(parents=True)
    pd.DataFrame({"date": CAL}).to_parquet(WORK / "meta" / "trade_calendar.parquet")
    pd.DataFrame({"date": CAL, "symbol": "600519"}).to_parquet(
        WORK / "daily" / "600519__qfq.parquet")
    for d in CAL:
        pd.DataFrame({"symbol": ["600519"], "date": d}).to_parquet(
            WORK / "market" / "daily_by_date" / f"{d.date()}.parquet")

    calls = []
    orig_map = dict(storage._DIR_MAP)
    orig_dir, orig_fetch, orig_lc = snapshot.STORE_DIR, market.fetch_kline, market.last_closed_trade_date
    try:
        storage._DIR_MAP["daily"] = WORK / "daily"
        storage._DIR_MAP["meta"] = WORK / "meta"
        snapshot.STORE_DIR = WORK
        snapshot._trading_days = lambda store=None: CAL
        market.last_closed_trade_date = lambda store=None: pd.Timestamp("2026-11-10")
        market.fetch_kline = lambda code, **kw: (calls.append(code), pd.DataFrame())[1]
        r = market.update_kline_batch(["600519"], store=ParquetStore(), source="bs")
    finally:
        storage._DIR_MAP.clear()
        storage._DIR_MAP.update(orig_map)
        snapshot.STORE_DIR = orig_dir
        snapshot._trading_days = lambda store=None: CAL
        market.fetch_kline, market.last_closed_trade_date = orig_fetch, orig_lc
        shutil.rmtree(WORK, ignore_errors=True)

    assert calls == [], calls
    assert r.get("600519") == "uptodate", r

