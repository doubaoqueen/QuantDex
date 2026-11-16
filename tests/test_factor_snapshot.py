"""宽面板因子快照回归（D-084，2026-11-17）：向量化口径与逐只口径逐值等价 + 缓存往返。

背景：candidates 原逐只循环 949 只 ≈5~10s（调用开销主导，纯数学仅 0.19s）；
改宽面板快照（factor_last_row_wide）+ 日更预计算缓存。本文件核验三件事：
① 快照与 compute_raw_factors 逐只口径逐值等价（含晚上市/中途停牌/停牌到尾三种形态）；
② ≥70 行过滤、pct_chg 小数口径、无分红时 dy_ttm 缺失语义；
③ 缓存落盘/命中/失效（as_of 口径）。
"""
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis import factors
from src.analysis.factors import (
    compute_raw_factors,
    factor_last_row_wide,
    factor_snapshot_wide,
    get_factor_snapshot,
)

DATES = pd.bdate_range("2026-01-01", periods=400)


def _mk_frame(dates, seed, start=20.0):
    """合成日线帧：几何随机游走 + 正换手/成交额（pct_chg 为百分数口径，与仓库一致）。"""
    rng = np.random.default_rng(seed)
    n = len(dates)
    close = start * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    turnover = rng.uniform(0.3, 5.0, n)
    amount = close * rng.uniform(5e6, 5e7, n)
    pct = np.concatenate([[0.0], (close[1:] / close[:-1] - 1) * 100])
    return pd.DataFrame({"date": pd.DatetimeIndex(dates), "close": close,
                         "amount": amount, "turnover": turnover, "pct_chg": pct})


def _synthetic_frames():
    """七种形态：A/B 正常全史；C 晚上市；D 中途停牌 20 日；E 只有 50 行；F 停牌到尾；
    G 停牌占位行（close 冻结但 turnover/amount 全 NaN——000016 型，必须被剔除）。"""
    a = _mk_frame(DATES, seed=1)
    b = _mk_frame(DATES, seed=2, start=8.0)
    c = _mk_frame(DATES[100:], seed=3, start=50.0)                       # 晚上市
    d_dates = DATES.drop(DATES[200:220])                                 # 中途停牌
    d = _mk_frame(d_dates, seed=4, start=15.0)
    e = _mk_frame(DATES[:50], seed=5)                                    # <70 行，应被剔除
    f = _mk_frame(DATES[:350], seed=6, start=30.0)                       # 停牌到尾
    g = _mk_frame(DATES[:380], seed=7, start=2.5)
    gmask = g["date"].isin(DATES[350:])                                  # 停牌占位：尾部 30 行值全坏
    g.loc[gmask, "amount"] = np.nan
    g.loc[gmask, "turnover"] = np.nan
    g.loc[gmask, "close"] = float(g["close"].iloc[349])                  # close 冻结（占位行特征）
    g.loc[gmask, "pct_chg"] = 0.0
    out = {"600000": a, "600001": b, "600002": c, "600003": d,
           "600004": e, "600005": f, "600006": g}
    for sym, frame in out.items():
        frame["symbol"] = sym
    return out


def _assert_row_equal(row, last, label):
    for col in ("close", "turnover_20", "vol_20", "rev_5", "mom_20",
                "mom_60", "rsi_14", "ma60_bias", "amihud_20"):
        a, b = float(row[col]), float(last[col])
        assert np.isclose(a, b, rtol=1e-9, atol=1e-12), f"{label} {col}: {a} != {b}"


def test_wide_matches_persymbol():
    frames = _synthetic_frames()
    snap = factor_last_row_wide(list(frames.values()))
    assert snap.index.name == "symbol"
    assert "600004" not in snap.index, "50 行标的必须被 ≥70 行规则剔除"
    assert pd.isna(snap.loc["600006", "turnover_20"]), (
        "停牌占位型（close 冻结、turnover 全 NaN）的 turnover_20 必须被新鲜度约束置 NaN"
        "——candidates 层 isfinite 过滤据此剔除，与旧口径一致")
    assert set(snap.index) == {"600000", "600001", "600002", "600003", "600005", "600006"}
    for sym, frame in frames.items():
        if sym not in snap.index:
            continue
        raw = compute_raw_factors(frame.sort_values("date").tail(420).reset_index(drop=True))
        last = raw.iloc[-1]
        if not all(np.isfinite(last[c]) for c in ("turnover_20", "vol_20", "rev_5")):
            # 旧口径本就剔除（停牌占位型）——快照应同为 NaN，不允许被旧值"救活"
            assert pd.isna(snap.loc[sym, "turnover_20"]), f"{sym} 停牌占位型未被剔除"
            continue
        _assert_row_equal(snap.loc[sym], raw.iloc[-1], sym)
        # pct_chg：仓库百分数 → 快照小数
        assert np.isclose(float(snap.loc[sym, "pct_chg"]),
                          float(frame["pct_chg"].iloc[-1]) / 100.0, rtol=1e-12)
    # F 停牌到尾：close 取它自己的最后值（不是全局末日的 NaN/传染值）
    assert np.isclose(float(snap.loc["600005", "close"]),
                      float(frames["600005"]["close"].iloc[-1]))
    # as_of = 宽矩阵最后交易日；last_dates[F] = 它自己的最后交易日
    assert snap.attrs["as_of"] == DATES[-1]
    assert snap.attrs["last_dates"]["600005"] == DATES[349]
    assert snap.attrs["last_dates"]["600000"] == DATES[-1]


class _FakeStore:
    """鸭子类型仓库：只实现 factor_snapshot_wide 需要的 read。read_counts 可观测调用。"""

    def __init__(self, files: dict):
        self.files = files
        self.read_counts = {}

    def read(self, kind, name, columns=None):
        key = f"{kind}/{name}"
        self.read_counts[key] = self.read_counts.get(key, 0) + 1
        df = self.files.get(key)
        if df is None:
            raise FileNotFoundError(key)
        return df.copy()


def test_snapshot_wide_dy_ttm_empty_semantics():
    frames = _synthetic_frames()
    store = _FakeStore({f"daily/{s}__qfq": v for s, v in frames.items()})
    snap = factor_snapshot_wide(list(frames), store=store)
    assert "dy_ttm" in snap.columns
    assert snap["dy_ttm"].isna().all(), "无分红数据时 dy_ttm 全 NaN（红利账户按 >0 过滤）"


def test_snapshot_cache_roundtrip():
    tmp = Path(tempfile.mkdtemp(prefix="qd_snap_test_"))
    orig = factors.SNAPSHOT_PATH
    factors.SNAPSHOT_PATH = tmp / "factor_snapshot.parquet"
    try:
        snap = factor_last_row_wide(list(_synthetic_frames().values()))
        factors._save_snapshot_cache(snap, "2026-01-30")
        hit = factors._load_snapshot_cache(pd.Timestamp("2026-01-30"))
        assert hit is not None
        pd.testing.assert_frame_equal(hit, snap, check_freq=False)
        assert hit.attrs["as_of"] == pd.Timestamp("2026-01-30")
        assert factors._load_snapshot_cache(pd.Timestamp("2026-01-31")) is None, "过期缓存必须失效"
        assert factors._load_snapshot_cache(None) is None
    finally:
        factors.SNAPSHOT_PATH = orig
        shutil.rmtree(tmp, ignore_errors=True)


def test_get_factor_snapshot_cache_hit_and_refresh():
    tmp = Path(tempfile.mkdtemp(prefix="qd_snap_test_"))
    orig = factors.SNAPSHOT_PATH
    factors.SNAPSHOT_PATH = tmp / "factor_snapshot.parquet"
    try:
        frames = _synthetic_frames()
        store = _FakeStore({f"daily/{s}__qfq": v for s, v in frames.items()})
        syms = list(frames)
        d1 = pd.Timestamp("2026-02-02")
        d2 = pd.Timestamp("2026-02-03")

        s1 = get_factor_snapshot(syms, store=store, latest=d1)
        n_first = sum(v for k, v in store.read_counts.items() if k.startswith("daily/6000"))
        assert len(s1) == 6 and s1.attrs["as_of"] == d1

        s2 = get_factor_snapshot(syms, store=store, latest=d1)   # 同日 → 缓存命中
        n_second = sum(v for k, v in store.read_counts.items() if k.startswith("daily/6000"))
        assert n_second == n_first, "缓存命中不应再读任何日线文件"
        pd.testing.assert_frame_equal(s2, s1, check_freq=False)

        s3 = get_factor_snapshot(syms, store=store, latest=d2)   # 新交易日 → 缓存失效重算
        n_third = sum(v for k, v in store.read_counts.items() if k.startswith("daily/6000"))
        assert n_third > n_second, "as_of 推进后必须重算"
        assert s3.attrs["as_of"] == d2
    finally:
        factors.SNAPSHOT_PATH = orig
        shutil.rmtree(tmp, ignore_errors=True)
