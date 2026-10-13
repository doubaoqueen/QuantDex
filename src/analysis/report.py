# -*- coding: utf-8 -*-
"""
市场与数据分析报告：单标的收益/风险概览、分年度收益、滚动区间收益、
与基准的相关性/Beta、数据仓库覆盖情况、全市场涨跌家数。
"""
import numpy as np
import pandas as pd

from config import TRADING_DAYS_PER_YEAR
from src.data.storage import ParquetStore


def yearly_returns(df: pd.DataFrame) -> pd.Series:
    """分年度收益率（按收盘日收益复利）。"""
    out = df.copy()
    out["ret"] = out["close"].pct_change().fillna(0)
    out["year"] = pd.to_datetime(out["date"]).dt.year
    return out.groupby("year")["ret"].apply(lambda r: (1 + r).prod() - 1)


def rolling_window_returns(df: pd.DataFrame,
                           windows=(20, 60, 120, 250)) -> dict:
    """近 N 个交易日收益率。"""
    close = df["close"]
    result = {}
    for n in windows:
        if len(close) > n:
            result[f"近{n}日"] = close.iloc[-1] / close.iloc[-n - 1] - 1
    return result


def symbol_overview(df: pd.DataFrame, benchmark: pd.DataFrame | None = None,
                    rf: float = 0.018) -> dict:
    """单标的收益/风险概览。benchmark 为标准化基准日线（可选）。"""
    close = df["close"]
    rets = close.pct_change().dropna()
    n = len(rets)
    total = close.iloc[-1] / close.iloc[0] - 1
    ann = (1 + total) ** (TRADING_DAYS_PER_YEAR / n) - 1
    vol = rets.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (ann - rf) / vol if vol > 0 else np.nan
    nav = (1 + rets).cumprod()
    mdd = (nav / nav.cummax() - 1).min()
    calmar = ann / abs(mdd) if mdd < 0 else np.nan

    overview = {
        "区间交易日": n,
        "区间总收益": total,
        "年化收益": ann,
        "年化波动": vol,
        "夏普比率": sharpe,
        "最大回撤": mdd,
        "Calmar比率": calmar,
    }
    overview.update(rolling_window_returns(df))

    if benchmark is not None and len(benchmark) > 2:
        b = benchmark[["date", "close"]].rename(columns={"close": "bench_close"})
        m = df[["date", "close"]].merge(b, on="date", how="inner")
        if len(m) > 30:
            r1 = m["close"].pct_change()
            r2 = m["bench_close"].pct_change()
            joined = pd.concat([r1, r2], axis=1).dropna()
            joined.columns = ["s", "b"]
            cov = joined.cov().iloc[0, 1]
            var_b = joined["b"].var()
            overview["与基准相关系数"] = joined.corr().iloc[0, 1]
            overview["相对基准Beta"] = cov / var_b if var_b > 0 else np.nan
            overview["基准区间收益"] = m["bench_close"].iloc[-1] / m["bench_close"].iloc[0] - 1
            overview["超额收益(区间)"] = (m["close"].iloc[-1] / m["close"].iloc[0] - 1) - overview["基准区间收益"]
    return overview


def store_coverage(store: ParquetStore | None = None) -> pd.DataFrame:
    """数据仓库覆盖情况：各数据集的数量、总行数、日期范围。"""
    store = store or ParquetStore()
    reg = store.registry()
    if reg.empty:
        return reg
    g = reg.groupby("dataset").agg(
        数据集数量=("name", "count"),
        总行数=("rows", "sum"),
        最早日期=("min_date", "min"),
        最晚日期=("max_date", "max"),
    )
    return g.sort_values("dataset")


def market_breadth(spot: pd.DataFrame) -> dict:
    """全市场涨跌家数统计（输入东财快照，含'涨跌幅'列）。"""
    if "涨跌幅" not in spot.columns:
        return {}
    pct = pd.to_numeric(spot["涨跌幅"], errors="coerce").dropna()
    return {
        "总数": len(pct),
        "上涨": int((pct > 0).sum()),
        "下跌": int((pct < 0).sum()),
        "平盘": int((pct == 0).sum()),
        "涨停(>9.8%)": int((pct >= 9.8).sum()),
        "跌停(<-9.8%)": int((pct <= -9.8).sum()),
        "涨跌幅中位数": float(pct.median()),
    }
