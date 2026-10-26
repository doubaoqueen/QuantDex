"""
数据质量检查：缺失交易日、重复日期、零成交(停牌)、OHLC 逻辑错误、
缺失值统计、异常涨跌幅。
"""
import numpy as np
import pandas as pd

from src.data.storage import ParquetStore


def check_duplicates(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    """重复的日期行。"""
    return df[df.duplicated(subset=[date_col], keep=False)].copy()


def check_missing_dates(df: pd.DataFrame, calendar: pd.DataFrame | None = None,
                        store: ParquetStore | None = None,
                        date_col: str = "date") -> pd.DataFrame:
    """
    区间内对照交易日历缺失的交易日（可能是未抓取或停牌，需结合零成交判断）。
    """
    store = store or ParquetStore()
    if calendar is None:
        calendar = store.read("meta", "trade_calendar")
    cal_dates = pd.to_datetime(calendar["date"])
    have = pd.to_datetime(df[date_col])
    lo, hi = have.min(), have.max()
    expected = cal_dates[(cal_dates >= lo) & (cal_dates <= hi)]
    missing = sorted(set(expected) - set(have))
    return pd.DataFrame({"missing_date": missing})


def check_zero_volume(df: pd.DataFrame) -> pd.DataFrame:
    """零成交量/零成交额（停牌典型特征）。"""
    cols = [c for c in ("volume", "amount") if c in df.columns]
    if not cols:
        return pd.DataFrame()
    mask = False
    for c in cols:
        m = pd.to_numeric(df[c], errors="coerce").fillna(0) == 0
        mask = m if mask is False else (mask | m)
    return df[mask].copy()


def check_ohlc_logic(df: pd.DataFrame) -> pd.DataFrame:
    """OHLC 价格逻辑错误：最高<最低、开/收 超出最高最低范围。"""
    o, h, ln, c = (pd.to_numeric(df[c], errors="coerce")
                  for c in ("open", "high", "low", "close"))
    bad = (h < ln) | (o > h) | (o < ln) | (c > h) | (c < ln) | h.isna() | ln.isna()
    out = df[bad].copy()
    if len(out):
        out["issue"] = np.select(
            [h[bad] < ln[bad],
             (o[bad] > h[bad]) | (o[bad] < ln[bad]),
             (c[bad] > h[bad]) | (c[bad] < ln[bad])],
            ["high<low", "open_out_of_range", "close_out_of_range"],
            default="missing_hl",
        )
    return out


def check_extreme_move(df: pd.DataFrame, limit_pct: float = 21.0) -> pd.DataFrame:
    """
    涨跌幅超过 limit_pct%（A股最宽为创业板/科创板 20%，留 1% 容差）。
    可能是数据错误或复权切换边界。
    """
    if "pct_chg" not in df.columns:
        return pd.DataFrame()
    pct = pd.to_numeric(df["pct_chg"], errors="coerce")
    return df[pct.abs() > limit_pct].copy()


def missing_summary(df: pd.DataFrame) -> pd.DataFrame:
    """每列缺失数量与缺失率。"""
    n = len(df)
    rows = []
    for c in df.columns:
        miss = int(df[c].isna().sum())
        rows.append({"column": c, "missing": miss,
                     "missing_pct": round(miss / n, 4) if n else 0})
    return pd.DataFrame(rows)


def quality_report(df: pd.DataFrame, store: ParquetStore | None = None,
                   symbol: str = "") -> tuple[dict, dict]:
    """
    生成数据质检报告。
    返回 (摘要 dict, 明细 dict[问题名 -> DataFrame])。
    """
    store = store or ParquetStore()
    date_col = "datetime" if "datetime" in df.columns else "date"

    dup = check_duplicates(df, date_col)
    zero = check_zero_volume(df)
    logic = check_ohlc_logic(df)
    extreme = check_extreme_move(df)
    miss_summary = missing_summary(df)

    try:
        missing_dates = check_missing_dates(df, store=store, date_col=date_col)
    except FileNotFoundError:
        missing_dates = pd.DataFrame()

    summary = {
        "标的": symbol,
        "总行数": len(df),
        "起始": str(pd.to_datetime(df[date_col]).min()),
        "截止": str(pd.to_datetime(df[date_col]).max()),
        "重复日期": len(dup),
        "缺失交易日": len(missing_dates),
        "零成交(疑似停牌)": len(zero),
        "OHLC逻辑错误": len(logic),
        "异常涨跌幅(>21%)": len(extreme),
    }
    details = {
        "duplicates": dup,
        "missing_dates": missing_dates,
        "zero_volume": zero,
        "ohlc_logic": logic,
        "extreme_move": extreme,
        "missing_summary": miss_summary,
    }
    return summary, details
