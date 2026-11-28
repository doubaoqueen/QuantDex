"""
涨停事件扫描：从 5 分钟 bar 识别触板 / 开板确认买入，并给出 T+1 出场收益。

为什么要下沉到 src（D-118）：2026-11-21~26 的审计发现，54/55 号脚本里
"涨停价"用 9.5%/19.5% 缓冲口径（伪触板 155/369）、"T-1 换手"从分钟线读而该列全空
（过滤恒通过）、"T+1 开盘无条件卖"实际实现成了 -9%/+14% 止损止盈——三处都与
预注册规则不符，最终裁决因此建立在错误的证据上。规则写进本模块并配单元测试，
脚本只做编排与报表。

口径（预注册，D-103；此处为修正版）：
  触板     bar.high >= 真实涨停价 = round(前收 × (1+幅度), 2)，幅度主板 10% / 创科 20%
  首触     当日第一根触板 bar 的时间，必须 <= 10:00（早盘异动的可执行性前提）
  非一字   当日开盘价 < 涨停价（一字板买不到）
  低换手   **前一日**全天换手 < 3%（当日换手在 10:00 时点不可知，用过即未来函数）
  介入     首触之后（含首触 bar）第一根收盘价 < 涨停价的 bar，按该 bar 收盘价成交
           ——"开板确认买入"的近似；全程封死则当日不可买（不计入事件）
  出场     T+1 开盘无条件卖出（T+1 开盘跌停则退化为 T+1 收盘，现实自适应）
  成本     20.2bp/回合 = 佣金 2×2.5bp + 滑点 2×5bp + 印花税 5bp + 过户费 2×0.1bp
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.analysis.factor_test import _newey_west_tstat

# 20.2bp/回合（D-102 更正了 D-095 的 "22.7bp" 误标）
COST_RT = 0.00025 * 2 + 0.0005 * 2 + 0.0005 + 0.00001 * 2
TOUCH_EPS = 0.005          # 价格比较容差（元）：四舍五入到分后的 0.005 内视为同一价
FIRST_TOUCH_DEADLINE = "10:00"
LOW_TURNOVER_PCT = 3.0     # T-1 全天换手率上限（%）


def limit_ratio(code: str) -> float:
    """涨跌停幅度：创业板/科创板 20%，其余主板 10%（北交所不在本模块口径内）。"""
    return 0.20 if str(code).startswith(("30", "68")) else 0.10


def limit_price(prev_close: float, code: str) -> float:
    """真实涨停价：交易所按分四舍五入。**不要用 prev×(1+幅度-0.5%) 之类的缓冲口径。**"""
    return round(float(prev_close) * (1.0 + limit_ratio(code)), 2)


@dataclass
class DayBar:
    """单只股票单日的 bar 骨架（避免逐日 pandas 索引开销）。"""

    date: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    amount: float
    minutes: np.ndarray      # 每根 bar 的"当日第几分钟"（9:35 → 575）
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray


def day_bars(df: pd.DataFrame) -> list[DayBar]:
    """分钟 bar 帧（datetime/open/high/low/close/amount）→ 按日骨架列表。"""
    d = df.copy()
    d["dt"] = pd.to_datetime(d["datetime"])
    d = d.sort_values("dt")
    d["date"] = d["dt"].dt.normalize()
    d["mins"] = d["dt"].dt.hour * 60 + d["dt"].dt.minute
    out = []
    for date, g in d.groupby("date", sort=True):
        out.append(DayBar(
            date=pd.Timestamp(date),
            open=float(g["open"].iloc[0]), high=float(g["high"].max()),
            low=float(g["low"].min()), close=float(g["close"].iloc[-1]),
            amount=float(g["amount"].sum()),
            minutes=g["mins"].to_numpy(dtype=int),
            o=g["open"].to_numpy(float), h=g["high"].to_numpy(float),
            lo=g["low"].to_numpy(float), c=g["close"].to_numpy(float)))
    return out


def turnover_by_date(store, code: str) -> dict:
    """从**日线**读该标的的换手率（%），返回 {"YYYY-MM-DD": 换手率}。

    为什么必须读日线（D-118）：分钟线的 turnover 列在仓库里全为空（baostock 分钟
    接口不带换手），54/55 号脚本对它求和得 0，"T-1 换手 < 3%" 恒成立——过滤是空操作。
    """
    from src.data.market import get_kline

    try:
        df = get_kline(code, adjust="qfq", store=store)
    except FileNotFoundError:
        return {}
    d = df[["date", "turnover"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    d["turnover"] = pd.to_numeric(d["turnover"], errors="coerce")
    d = d.dropna().drop_duplicates("date").sort_values("date")
    return {t.strftime("%Y-%m-%d"): float(v) for t, v in zip(d["date"], d["turnover"], strict=False)}


def calendar_positions(store) -> dict:
    """交易日历 → {date: 序号}（连续性校验用）。"""
    cal = pd.read_parquet(store._path("meta", "trade_calendar"), columns=["date"])   # noqa: SLF001
    idx = pd.DatetimeIndex(sorted(pd.to_datetime(cal["date"]).unique()))
    return {d: i for i, d in enumerate(idx)}


def scan_universe(store, symbols, *, start: str = "2020-01-01",
                  min_bars: int = 200, apply_turnover_filter: bool = False,
                  pit: pd.DataFrame | None = None, st_flag: pd.DataFrame | None = None,
                  progress_every: int = 150, log=print) -> pd.DataFrame:
    """对一组标的逐只扫描事件（读分钟仓库 → dip_entry → attach_exit）。

    apply_turnover_filter=False 时返回全部信号（含高换手），换手过滤在调用方做——
    这样同一份扫描结果可以同时支持"预注册口径"与"不过滤"的敏感性分析。

    pit:     历史成分表（symbol/in_date/out_date）；给出时只扫描该标的在指数内的
             交易日（与因子类检验同一股票池口径）。
    st_flag: 按日 ST 标记（symbol/date/is_st）；给出时剔除**当日**为 ST 的交易日。
    """
    cal_pos = calendar_positions(store)
    pit_ranges = _pit_ranges(pit)
    st_days = _st_day_sets(st_flag)
    frames = []
    for i, code in enumerate(sorted(map(str, symbols)), 1):
        try:
            bars = day_bars(get_minute(store, code))
        except FileNotFoundError:
            continue
        bars = [b for b in bars if b.date >= pd.Timestamp(start)]
        if pit_ranges is not None:
            rng = pit_ranges.get(code)
            if rng is None:
                continue
            lo, hi = rng
            bars = [b for b in bars if b.date >= lo and (hi is None or b.date < hi)]
        if st_days is not None:
            bad = st_days.get(code, frozenset())
            bars = [b for b in bars if b.date not in bad]
        if len(bars) < min_bars:
            continue
        turn = turnover_by_date(store, code)
        ev = dip_entry(bars, code, turnover_by_date=turn, calendar_pos=cal_pos)
        if ev:
            frames.append(attach_exit(ev, bars, code, calendar_pos=cal_pos))
        if i % progress_every == 0:
            log(f"   {i}/{len(symbols)}")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    if apply_turnover_filter:
        out = out[out["t1_turnover"] < LOW_TURNOVER_PCT].reset_index(drop=True)
    return out


def _pit_ranges(pit: pd.DataFrame | None) -> dict | None:
    """历史成分表 → {symbol: (in_date, out_date|None)}；None = 不做成员期过滤。"""
    if pit is None or len(pit) == 0:
        return None
    p = pit.copy()
    p["symbol"] = p["symbol"].astype(str)
    p["in_date"] = pd.to_datetime(p["in_date"])
    p["out_date"] = pd.to_datetime(p["out_date"])
    out = {}
    for sym, g in p.groupby("symbol"):
        outs = g["out_date"].dropna()
        out[sym] = (g["in_date"].min(), outs.max() if len(outs) else None)
    return out


def _st_day_sets(st_flag: pd.DataFrame | None) -> dict | None:
    """按日 ST 标记 → {symbol: frozenset(处于 ST 的交易日)}；None = 不做 ST 过滤。"""
    if st_flag is None or len(st_flag) == 0:
        return None
    f = st_flag.copy()
    f["symbol"] = f["symbol"].astype(str)
    f = f[pd.to_numeric(f["is_st"], errors="coerce") == 1]
    return {sym: frozenset(pd.to_datetime(g["date"])) for sym, g in f.groupby("symbol")}


def get_minute(store, code: str) -> pd.DataFrame:
    """读某只标的的 5 分钟 bar（未复权口径，与 29 号脚本的存储一致）。"""
    return store.read("minute", f"{code}__5__none",
                      columns=["datetime", "open", "high", "low", "close", "amount"])


def _deadline_minutes() -> int:
    t = pd.Timestamp(FIRST_TOUCH_DEADLINE)
    return t.hour * 60 + t.minute


def dip_entry(bars: list[DayBar], code: str,
              turnover_by_date: dict | None = None,
              calendar_pos: dict | None = None) -> list[dict]:
    """单只股票的事件扫描：返回每个"开板确认买入"信号（出场另见 attach_exit）。

    turnover_by_date: {"YYYY-MM-DD": T-1 全天换手率(%)}——键用日期字符串，避免
                      Timestamp 规范化差异；None 表示不做换手过滤（turnover_ok=None）。
    calendar_pos:     {date: 交易日序号}——用来校验"前一日"确为上一交易日（停牌缺口剔除）；
                      None 时不做连续性校验。
    """
    deadline = _deadline_minutes()
    events = []
    for i in range(1, len(bars)):
        bar, prev = bars[i], bars[i - 1]
        if calendar_pos is not None:
            p0, p1 = calendar_pos.get(prev.date), calendar_pos.get(bar.date)
            if p0 is None or p1 is None or (p1 - p0) != 1:
                continue
        lp = limit_price(prev.close, code)
        if bar.open >= lp - TOUCH_EPS:            # 一字板：买不到
            continue
        touched = (bar.h >= lp - TOUCH_EPS) & (bar.minutes <= deadline)
        if not touched.any():
            continue
        k0 = int(np.flatnonzero(touched)[0])
        below = np.flatnonzero(bar.c[k0:] < lp - TOUCH_EPS)
        if len(below) == 0:                       # 全程封死，买不到
            continue
        k = k0 + int(below[0])
        t1_turn = (turnover_by_date or {}).get(prev.date.strftime("%Y-%m-%d"))
        events.append({
            "symbol": str(code), "date": bar.date,
            "limit_price": lp, "entry": float(bar.c[k]),
            "entry_minutes": int(bar.minutes[k]),
            "first_touch_minutes": int(bar.minutes[k0]),
            "sealed": bool(bar.close >= lp - TOUCH_EPS),
            "prev_close": float(prev.close), "t1_turnover": t1_turn,
            "entry_bar_amount": float(bar.amount),
            "_next_index": i + 1 if i + 1 < len(bars) else None,
        })
    return events


def attach_exit(events: list[dict], bars: list[DayBar], code: str,
                calendar_pos: dict | None = None) -> pd.DataFrame:
    """给事件补 T+1 出场：开盘卖；T+1 开盘跌停 → 退化为 T+1 收盘（现实自适应）。"""
    rows = []
    for e in events:
        j = e["_next_index"]
        if j is None:
            continue                          # 数据末日的信号无 T+1 出场，丢弃
        nxt = bars[j]
        if calendar_pos is not None:
            p0, p1 = calendar_pos.get(e["date"]), calendar_pos.get(nxt.date)
            if p0 is None or p1 is None or (p1 - p0) != 1:
                continue                      # 下一行不是下一交易日（停牌缺口）
        limit_down = round(bars[j - 1].close * (1 - limit_ratio(code)), 2)
        fallback = bool(nxt.o[0] <= limit_down + TOUCH_EPS)
        exit_px = float(nxt.c[-1] if fallback else nxt.o[0])
        rows.append({**{k: v for k, v in e.items() if not k.startswith("_")},
                     "exit_date": nxt.date, "exit_price": exit_px,
                     "exit_fallback": fallback, "gross": exit_px / e["entry"] - 1.0})
    out = pd.DataFrame(rows)
    if len(out):
        out["net"] = out["gross"] - COST_RT
    return out


def summarize(ev: pd.DataFrame, col: str = "net",
              slices=(("全样本 2020-26", 2020, 2026), ("样本内 2020-23", 2020, 2023),
                      ("样本外 2024-26", 2024, 2026))) -> dict:
    """分段统计：n / 均值 / 中位 / 胜率 / 逐日等权 NW-t（滞后 19）。"""
    out = {}
    if ev is None or len(ev) == 0:
        return out
    e = ev.copy()
    e["year"] = pd.to_datetime(e["date"]).dt.year
    for label, lo, hi in slices:
        sub = e[(e["year"] >= lo) & (e["year"] <= hi)]
        if len(sub) < 20:
            out[label] = {"n": int(len(sub)), "insufficient": True}
            continue
        daily = sub.groupby("date")[col].mean().sort_index()
        out[label] = {
            "n": int(len(sub)), "dates": int(sub["date"].nunique()),
            "mean": float(sub[col].mean()), "median": float(sub[col].median()),
            "win": float((sub[col] > 0).mean()),
            "nw_t_daily": float(_newey_west_tstat(daily, 19)),
        }
    return out


def cluster_t(ev: pd.DataFrame, col: str = "net") -> float:
    """按**日期聚类**的 t 值（同一天多个事件高度相关，逐事件 t 会虚高）。"""
    if ev is None or len(ev) <= 1:
        return float("nan")
    r = ev[col].to_numpy(float)
    mu = r.mean()
    s = pd.Series(r - mu).groupby(pd.to_datetime(ev["date"]).to_numpy()).sum().to_numpy()
    g = len(s)
    if g <= 1:
        return float("nan")
    se = np.sqrt((s ** 2).sum()) / len(r) * np.sqrt(g / (g - 1))
    return float(mu / se) if se > 0 else float("nan")
