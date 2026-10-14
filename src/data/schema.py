# -*- coding: utf-8 -*-
"""
数据标准化：代码格式转换、列名映射、类型清洗。
全系统内部统一使用 6 位纯代码（如 600519 / 510300 / 000300）。
"""
import pandas as pd

# ---------------------------------------------------------------- 代码转换

def normalize_code(code) -> str:
    """把各种形式的代码统一为 6 位纯代码：sh.600519 / 600519.SH / sh600519 -> 600519。"""
    s = str(code).strip().lower()
    for prefix in ("sh.", "sz.", "bj.", "sh", "sz", "bj"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.split(".")[0]
    return s.zfill(6)[:6] if s.isdigit() else s


def to_sina_code(code) -> str:
    """6 位代码 -> 新浪代码（sh600519 / sz000001，无点）。"""
    c = normalize_code(code)
    return ("sh" if c[0] in ("6", "9", "5") else "sz") + c


def to_bs_code(code, is_index: bool = False) -> str:
    """6 位代码 -> baostock 代码（sh.xxx / sz.xxx）。"""
    c = normalize_code(code)
    if is_index:
        # 深证指数 399 开头在深交所，其余指数（上证/中证系列）在上交所
        return ("sz." + c) if c.startswith("399") else ("sh." + c)
    if c[0] in ("6", "9", "5"):      # 沪市股票/B股、沪市ETF(5开头)
        return "sh." + c
    if c[0] in ("0", "2", "3", "1"):  # 深市股票、深市ETF(1开头)
        return "sz." + c
    return "sh." + c


# ---------------------------------------------------------------- 行情列映射

# akshare 东财日线中文列 -> 标准英文列
AK_DAILY_MAP = {
    "日期": "date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_chg",
    "涨跌额": "change",
    "换手率": "turnover",
}

# akshare 东财分钟线中文列 -> 标准英文列
AK_MIN_MAP = {
    "时间": "datetime",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "涨跌幅": "pct_chg",
    "涨跌额": "change",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "换手率": "turnover",
}

# baostock 日线列 -> 标准列
BS_DAILY_MAP = {
    "date": "date",
    "code": "symbol",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "amount": "amount",
    "turn": "turnover",
    "pctChg": "pct_chg",
}

DAILY_STD_COLS = ["symbol", "date", "open", "high", "low", "close",
                  "volume", "amount", "amplitude", "pct_chg", "change", "turnover"]
MIN_STD_COLS = ["symbol", "datetime", "open", "high", "low", "close",
                "volume", "amount", "amplitude", "pct_chg", "change", "turnover"]

_NUM_COLS = ["open", "high", "low", "close", "volume", "amount",
             "amplitude", "pct_chg", "change", "turnover"]


def standardize_ak_daily(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """标准化 akshare 东财日线（个股/ETF/指数通用）。"""
    out = df.rename(columns=AK_DAILY_MAP).copy()
    out["symbol"] = normalize_code(symbol)
    out["date"] = pd.to_datetime(out["date"])
    for c in _NUM_COLS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    for c in DAILY_STD_COLS:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[DAILY_STD_COLS].sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return out


def standardize_ak_min(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """标准化 akshare 东财分钟线。"""
    out = df.rename(columns=AK_MIN_MAP).copy()
    out["symbol"] = normalize_code(symbol)
    out["datetime"] = pd.to_datetime(out["datetime"])
    for c in _NUM_COLS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    for c in MIN_STD_COLS:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[MIN_STD_COLS].sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    return out


def standardize_bs_daily(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """标准化 baostock 日线（含指数）。"""
    out = df.rename(columns=BS_DAILY_MAP).copy()
    out["symbol"] = normalize_code(symbol)
    out["date"] = pd.to_datetime(out["date"])
    for c in _NUM_COLS:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    for c in DAILY_STD_COLS:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[DAILY_STD_COLS].sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return out


def standardize_sina_daily(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """标准化 akshare 新浪日线 stock_zh_a_daily（个股，东财不可用时的稳定后端）。"""
    out = df.copy()
    out["symbol"] = normalize_code(symbol)
    out["date"] = pd.to_datetime(out["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    # 新浪 turnover 为比例(0.0025)，统一为百分比口径(0.25)，与东财/baostock 一致
    if "turnover" in out.columns:
        out["turnover"] = pd.to_numeric(out["turnover"], errors="coerce") * 100
    out = out.sort_values("date").drop_duplicates("date")
    out["pct_chg"] = out["close"].pct_change() * 100
    out["change"] = out["close"].diff()
    out["amplitude"] = (out["high"] - out["low"]) / out["close"].shift(1) * 100
    for c in DAILY_STD_COLS:
        if c not in out.columns:
            out[c] = pd.NA
    out = out[DAILY_STD_COLS].sort_values("date").reset_index(drop=True)
    return out
