"""按日 ST 剔除口径（D-118）：面板只剔除**当日确实 ST** 的股票日，不再用今日名单回溯。

审计背景（2026-11-26）：D-071 的剔除按今天的简称回溯全部历史，被抹掉的持仓里
策略真正持有过的 7 只现 ST 股 33 次持有全部早于戴帽日，35 只里 19 只已退市——
等于把"事后已知的输家"整段删除（剔除版比不剔除版高 5.8pp）。
"""

import numpy as np
import pandas as pd

from src.analysis.factors import (
    ST_FLAG_NAME,
    build_factor_panel,
    load_st_flag,
    st_exclusion_mask,
)


def _flag(rows):
    f = pd.DataFrame(rows, columns=["symbol", "date", "is_st"])
    f["date"] = pd.to_datetime(f["date"])
    return f


def test_pit_mask_drops_only_days_flagged_st():
    """有记录的股票日：只看当日 is_st；记录为 0 的天即使今日叫 ST 也保留。"""
    dates = pd.Series(pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"]))
    symbols = pd.Series(["000001", "600079", "000001", "600079"])
    flag = _flag([("000001", "2024-01-02", 0), ("000001", "2024-01-03", 0),
                  ("600079", "2024-01-02", 0), ("600079", "2024-01-03", 1)])
    mask, diag = st_exclusion_mask(dates, symbols, flag, today_st={"600079"})
    assert list(mask) == [False, False, False, True]
    assert diag["mode"] == "pit" and diag["in_st"] == 1 and diag["fell_back"] == 0
    assert str(pd.Timestamp(diag["as_of"]).date()) == "2024-01-03"


def test_pit_mask_falls_back_to_today_name_when_row_missing():
    """表里没有这一行（未抓到 / 晚于表内最新日）→ 回退今日名单，方向保守。"""
    dates = pd.Series(pd.to_datetime(["2015-06-01", "2015-06-01"]))
    symbols = pd.Series(["000001", "600079"])
    flag = _flag([("000001", "2015-06-01", 0)])         # 600079 整只在表里没有
    mask, diag = st_exclusion_mask(dates, symbols, flag, today_st={"600079"})
    assert list(mask) == [False, True]
    assert diag["fell_back"] == 1 and diag["fell_back_dropped"] == 1
    assert diag["symbols_without_record"] == 1
    # 今日名单里没有它 → 回退后也不剔除
    mask2, diag2 = st_exclusion_mask(dates, symbols, flag, today_st=set())
    assert list(mask2) == [False, False]
    assert diag2["fell_back"] == 1 and diag2["fell_back_dropped"] == 0


def test_today_mode_keeps_old_behaviour_and_reports():
    """flag=None（表缺失或 --st-today）→ 今日名单回溯全部历史（D-071 旧口径，含前视）。"""
    dates = pd.Series(pd.to_datetime(["2015-06-01", "2015-06-01", "2024-01-02"]))
    symbols = pd.Series(["000001", "600079", "600079"])
    mask, diag = st_exclusion_mask(dates, symbols, None, today_st={"600079"})
    assert list(mask) == [False, True, True]
    assert diag["mode"] == "today" and diag["dropped"] == 2 and diag["rows"] == 3


class _StubStore:
    """最小 ParquetStore 鸭子类型：只提供 load_st_flag / build_factor_panel 用到的两个方法。"""

    def __init__(self, files=None):
        self.files = files or {}
        self.cache = None

    def exists(self, kind, name):
        return (kind, name) in self.files

    def read(self, kind, name, columns=None):
        if (kind, name) not in self.files:
            raise FileNotFoundError(f"{kind}/{name}")
        df = self.files[(kind, name)]
        return df[columns].copy() if columns else df.copy()


def test_load_st_flag_missing_returns_none():
    """表不存在 → None（调用方打印告警并回退今日口径，不许静默当成空表）。"""
    store = _StubStore()
    assert load_st_flag(store) is None
    store.files[("meta", ST_FLAG_NAME)] = pd.DataFrame(
        {"symbol": ["000001"], "date": [pd.Timestamp("2024-01-02")], "is_st": [1]})
    got = load_st_flag(store)
    assert got is not None and len(got) == 1 and int(got["is_st"].iloc[0]) == 1


def test_panel_exclude_st_uses_flag_not_today_name():
    """端到端：今天叫 ST 但当事日不是 ST 的股票必须留在面板里（D-118 的核心修复）。"""
    dates = pd.bdate_range("2024-01-02", periods=40)

    def _kline(code, px):
        return pd.DataFrame({"symbol": code, "date": dates, "open": px, "high": px + 1,
                             "low": px - 1, "close": px, "volume": 1e6, "amount": px * 1e6,
                             "turnover": 1.0})

    store = _StubStore({
        ("daily", "000001__qfq"): _kline("000001", 10.0 + np.arange(40) * 0.1),
        ("daily", "600079__qfq"): _kline("600079", 20.0 + np.arange(40) * 0.1),
        ("meta", "bs_securities"): pd.DataFrame({"symbol": ["000001", "600079"],
                                                 "name": ["平安银行", "ST人福"]}),
        # 按日标记：600079 在这 40 个交易日里全都不是 ST（戴帽远在其后）
        ("meta", ST_FLAG_NAME): pd.DataFrame({
            "symbol": np.repeat(["000001", "600079"], len(dates)),
            "date": np.tile(dates, 2), "is_st": 0}),
    })
    pit = build_factor_panel(store, ["000001", "600079"], start="2024-01-01", exclude_st=True)
    assert set(pit["symbol"]) == {"000001", "600079"}, "当事日非 ST → 必须保留"
    assert pit.attrs["st_filter"]["mode"] == "pit"
    old = build_factor_panel(store, ["000001", "600079"], start="2024-01-01", exclude_st="today")
    assert set(old["symbol"]) == {"000001"}, "今日名单口径（旧）会把整段历史抹掉"
    assert old.attrs["st_filter"]["mode"] == "today"
