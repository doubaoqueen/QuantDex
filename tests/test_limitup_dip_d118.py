"""涨停 dip-buy 事件扫描口径（D-118）：把 45~55 号里反复走样的三条规则钉死。

审计背景（2026-11-26）：54/55 号脚本与预注册规则有三处不符——
① 涨停价用 9.5%/19.5% 缓冲口径（伪触板 155/369，例：万科 2021-08-11 最高 +9.56%
   被算作触板，真实涨停是 +10%）；
② "T-1 换手 < 3%"从分钟线读，而该列全空 → 求和为 0 → 过滤恒通过；
③ "T+1 开盘无条件卖"实际实现成 -9%/+14% 止损止盈（归档中位数 -9.20% 就是止损线）。
本文件用手工构造的 bar 序列把正确口径钉住。
"""
import numpy as np
import pandas as pd

from src.data.limitup import (
    COST_RT,
    TOUCH_EPS,
    attach_exit,
    cluster_t,
    day_bars,
    dip_entry,
    limit_price,
    summarize,
)


def test_limit_price_uses_exchange_rounding():
    """真实涨停价按分四舍五入——不是 prev×(1+幅度-0.5%) 的缓冲口径。"""
    assert limit_price(10.00, "600000") == 11.00
    assert limit_price(21.86, "000002") == 24.05        # 21.86×1.1 = 24.046 → 24.05
    assert limit_price(9.99, "300750") == 11.99        # 创业板 20%：11.988 → 11.99
    assert limit_price(21.86, "000002") != round(21.86 * 1.095, 2)   # 24.05 ≠ 23.94


def _bars(spec):
    """spec: [(date, [(minute, open, high, low, close), ...]), ...] → 分钟 bar 帧。"""
    rows = []
    for date, bars in spec:
        for hhmm, o, h, low, c in bars:
            ts = pd.Timestamp(f"{date} {hhmm}")
            rows.append({"datetime": ts, "open": o, "high": h, "low": low, "close": c,
                         "amount": 1e7})
    return pd.DataFrame(rows)


def _cal(dates):
    return {pd.Timestamp(d): i for i, d in enumerate(dates)}


D1, D2, D3 = "2026-01-05", "2026-01-06", "2026-01-07"


def test_dip_entry_buys_on_first_close_below_limit():
    """首触 ≤10:00、开板后首个收盘低于涨停价 → 按该 bar 收盘价介入。"""
    # D1 收 10.00 → 次日涨停 11.00；9:40 触板（11.00）、9:50 开板收 10.80 → 介入 10.80
    df = _bars([(D1, [("09:35", 10.0, 10.1, 9.95, 10.05), ("15:00", 10.05, 10.1, 9.98, 10.0)]),
                (D2, [("09:35", 10.1, 10.5, 10.1, 10.4), ("09:40", 10.4, 11.0, 10.4, 11.0),
                      ("09:50", 11.0, 11.0, 10.7, 10.8), ("15:00", 10.8, 11.0, 10.7, 10.95)]),
                (D3, [("09:35", 10.9, 11.0, 10.8, 10.9), ("15:00", 10.9, 11.1, 10.9, 11.05)])])
    ev = dip_entry(day_bars(df), "600000", calendar_pos=_cal([D1, D2, D3]))
    assert len(ev) == 1
    e = ev[0]
    assert e["limit_price"] == 11.00
    assert np.isclose(e["entry"], 10.80), "开板确认价，不是触板价 11.00"
    assert e["first_touch_minutes"] == 9 * 60 + 40
    assert e["sealed"] is False                      # 收盘 10.95 < 11.00
    assert e["t1_turnover"] is None                  # 未传换手表


def test_one_word_board_and_late_touch_are_excluded():
    # 一字板：开盘就在涨停价 → 买不到
    df1 = _bars([(D1, [("09:35", 10.0, 10.0, 10.0, 10.0), ("15:00", 10.0, 10.0, 10.0, 10.0)]),
                 (D2, [("09:35", 11.0, 11.0, 11.0, 11.0), ("15:00", 11.0, 11.0, 11.0, 11.0)])])
    assert dip_entry(day_bars(df1), "600000", calendar_pos=_cal([D1, D2])) == []
    # 首触 10:30（>10:00）→ 不在可执行窗口
    df2 = _bars([(D1, [("09:35", 10.0, 10.0, 10.0, 10.0), ("15:00", 10.0, 10.0, 10.0, 10.0)]),
                 (D2, [("09:35", 10.1, 10.2, 10.0, 10.1), ("10:30", 10.2, 11.0, 10.2, 10.9),
                       ("15:00", 10.9, 10.95, 10.8, 10.9)])])
    assert dip_entry(day_bars(df2), "600000", calendar_pos=_cal([D1, D2])) == []
    # 触板后全程封死（没有再收在涨停价之下）→ 买不到
    df3 = _bars([(D1, [("09:35", 10.0, 10.0, 10.0, 10.0), ("15:00", 10.0, 10.0, 10.0, 10.0)]),
                 (D2, [("09:35", 10.1, 10.5, 10.1, 10.4), ("09:40", 10.4, 11.0, 10.4, 11.0),
                       ("15:00", 11.0, 11.0, 11.0, 11.0)])])
    assert dip_entry(day_bars(df3), "600000", calendar_pos=_cal([D1, D2])) == []


def test_turnover_filter_reads_previous_day():
    """t1_turnover 取**前一日**的换手率（当日换手在 10:00 不可知，用过即未来函数）。"""
    df = _bars([(D1, [("09:35", 10.0, 10.0, 10.0, 10.0), ("15:00", 10.0, 10.0, 10.0, 10.0)]),
                (D2, [("09:35", 10.1, 10.5, 10.1, 10.4), ("09:40", 10.4, 11.0, 10.4, 11.0),
                      ("09:50", 11.0, 11.0, 10.7, 10.8), ("15:00", 10.8, 10.9, 10.7, 10.8)])])
    turn = {D1: 1.2, D2: 9.9}                        # 当日换来 9.9% 也不能影响判定
    ev = dip_entry(day_bars(df), "600000", turnover_by_date=turn, calendar_pos=_cal([D1, D2]))
    assert len(ev) == 1 and np.isclose(ev[0]["t1_turnover"], 1.2)


def test_attach_exit_sells_next_open_and_falls_back_on_limit_down():
    df = _bars([(D1, [("09:35", 10.0, 10.0, 10.0, 10.0), ("15:00", 10.0, 10.0, 10.0, 10.0)]),
                (D2, [("09:35", 10.1, 10.5, 10.1, 10.4), ("09:40", 10.4, 11.0, 10.4, 11.0),
                      ("09:50", 11.0, 11.0, 10.7, 10.8), ("15:00", 10.8, 10.9, 10.7, 10.8)]),
                (D3, [("09:35", 11.2, 11.5, 11.1, 11.4), ("15:00", 11.4, 11.6, 11.3, 11.5)])])
    bars = day_bars(df)
    ev = attach_exit(dip_entry(bars, "600000", calendar_pos=_cal([D1, D2, D3])), bars, "600000",
                     calendar_pos=_cal([D1, D2, D3]))
    assert len(ev) == 1
    assert np.isclose(ev["exit_price"].iloc[0], 11.2), "T+1 开盘无条件卖"
    assert np.isclose(ev["gross"].iloc[0], 11.2 / 10.8 - 1)
    assert np.isclose(ev["net"].iloc[0], 11.2 / 10.8 - 1 - COST_RT)
    assert not bool(ev["exit_fallback"].iloc[0])


def test_attach_exit_degrades_to_close_when_t1_opens_limit_down():
    # D2 收 10.80 → D3 跌停价 round(10.80×0.9, 2) = 9.72；D3 开盘即跌停 → 退化为收盘
    df = _bars([(D1, [("09:35", 10.0, 10.0, 10.0, 10.0), ("15:00", 10.0, 10.0, 10.0, 10.0)]),
                (D2, [("09:35", 10.1, 10.5, 10.1, 10.4), ("09:40", 10.4, 11.0, 10.4, 11.0),
                      ("09:50", 11.0, 11.0, 10.7, 10.8), ("15:00", 10.8, 10.9, 10.7, 10.8)]),
                (D3, [("09:35", 9.72, 9.72, 9.72, 9.72), ("15:00", 9.72, 9.9, 9.72, 9.85)])])
    bars = day_bars(df)
    ev = attach_exit(dip_entry(bars, "600000", calendar_pos=_cal([D1, D2, D3])), bars, "600000",
                     calendar_pos=_cal([D1, D2, D3]))
    assert bool(ev["exit_fallback"].iloc[0]) is True
    assert np.isclose(ev["exit_price"].iloc[0], 9.85), "跌停开盘卖不出 → 退化为收盘价"


def test_calendar_gap_drops_signal():
    """前一日不是上一交易日（停牌缺口）→ 不产生信号（否则把旧收盘当昨收）。"""
    df = _bars([(D1, [("09:35", 10.0, 10.0, 10.0, 10.0), ("15:00", 10.0, 10.0, 10.0, 10.0)]),
                (D3, [("09:35", 10.1, 10.5, 10.1, 10.4), ("09:40", 10.4, 11.0, 10.4, 11.0),
                      ("09:50", 11.0, 11.0, 10.7, 10.8), ("15:00", 10.8, 10.9, 10.7, 10.8)])])
    cal = {pd.Timestamp(D1): 0, pd.Timestamp(D2): 1, pd.Timestamp(D3): 2}   # D2 无 bar
    assert dip_entry(day_bars(df), "600000", calendar_pos=cal) == []


def test_summarize_and_cluster_t_shapes():
    n = 60
    dates = pd.bdate_range("2024-01-02", periods=n)
    ev = pd.DataFrame({"date": dates, "net": np.full(n, -0.01),
                       "gross": np.full(n, -0.008)})
    s = summarize(ev, slices=(("all", 2024, 2026),))
    assert s["all"]["n"] == n
    assert np.isclose(s["all"]["mean"], -0.01)
    assert s["all"]["win"] == 0.0
    # 全部同值：方差为 0 → t 为 NaN 或 inf，不能是有效数字
    t = cluster_t(ev)
    assert np.isnan(t) or abs(t) > 1e6
    assert TOUCH_EPS == 0.005
