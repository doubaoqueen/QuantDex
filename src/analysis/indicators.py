# -*- coding: utf-8 -*-
"""
技术指标库（纯 pandas/numpy 实现，无外部依赖）。
输入为标准化日线 DataFrame（含 open/high/low/close/volume）。
所有函数返回带新列的 DataFrame，不修改原数据。
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 均线
def add_ma(df: pd.DataFrame, windows=(5, 10, 20, 60, 120, 250)) -> pd.DataFrame:
    out = df.copy()
    for n in windows:
        out[f"ma{n}"] = out["close"].rolling(n).mean()
    return out


def add_ema(df: pd.DataFrame, windows=(5, 10, 12, 20, 26, 60)) -> pd.DataFrame:
    out = df.copy()
    for n in windows:
        out[f"ema{n}"] = out["close"].ewm(span=n, adjust=False).mean()
    return out


# ---------------------------------------------------------------- MACD
def add_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    out = df.copy()
    ema_fast = out["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = out["close"].ewm(span=slow, adjust=False).mean()
    out["dif"] = ema_fast - ema_slow
    out["dea"] = out["dif"].ewm(span=signal, adjust=False).mean()
    out["macd"] = 2 * (out["dif"] - out["dea"])
    return out


# ---------------------------------------------------------------- RSI
def add_rsi(df: pd.DataFrame, windows=(6, 12, 24)) -> pd.DataFrame:
    out = df.copy()
    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    for n in windows:
        avg_gain = gain.rolling(n).mean()
        avg_loss = loss.rolling(n).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        out[f"rsi{n}"] = 100 - 100 / (1 + rs)
        out[f"rsi{n}"] = out[f"rsi{n}"].where(avg_loss != 0, 100)
    return out


# ---------------------------------------------------------------- KDJ
def add_kdj(df: pd.DataFrame, n=9) -> pd.DataFrame:
    out = df.copy()
    low_n = out["low"].rolling(n).min()
    high_n = out["high"].rolling(n).max()
    rsv = (out["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    # 国内行情软件通用 SMA(X,3,1)：alpha = 1/3
    out["k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    out["d"] = out["k"].ewm(alpha=1 / 3, adjust=False).mean()
    out["j"] = 3 * out["k"] - 2 * out["d"]
    return out


# ---------------------------------------------------------------- 布林带
def add_boll(df: pd.DataFrame, n=20, k=2.0) -> pd.DataFrame:
    out = df.copy()
    mid = out["close"].rolling(n).mean()
    std = out["close"].rolling(n).std(ddof=0)
    out["boll_mid"] = mid
    out["boll_up"] = mid + k * std
    out["boll_dn"] = mid - k * std
    return out


# ---------------------------------------------------------------- ATR
def add_atr(df: pd.DataFrame, n=14) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Wilder 平滑：alpha = 1/n
    out[f"atr{n}"] = tr.ewm(alpha=1 / n, adjust=False).mean()
    return out


# ---------------------------------------------------------------- 其他
def add_wr(df: pd.DataFrame, n=14) -> pd.DataFrame:
    out = df.copy()
    high_n = out["high"].rolling(n).max()
    low_n = out["low"].rolling(n).min()
    out[f"wr{n}"] = (high_n - out["close"]) / (high_n - low_n).replace(0, np.nan) * -100
    return out


def add_cci(df: pd.DataFrame, n=14) -> pd.DataFrame:
    out = df.copy()
    tp = (out["high"] + out["low"] + out["close"]) / 3
    ma = tp.rolling(n).mean()
    md = (tp - ma).abs().rolling(n).mean()
    out[f"cci{n}"] = (tp - ma) / (0.015 * md.replace(0, np.nan))
    return out


def add_obv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    sign = np.sign(out["close"].diff().fillna(0))
    out["obv"] = (sign * out["volume"]).cumsum()
    return out


def add_momentum(df: pd.DataFrame, n=20) -> pd.DataFrame:
    out = df.copy()
    out[f"mom{n}"] = out["close"] / out["close"].shift(n) - 1
    return out


def add_psy(df: pd.DataFrame, n=12) -> pd.DataFrame:
    out = df.copy()
    up = (out["close"].diff() > 0).astype(float)
    out[f"psy{n}"] = up.rolling(n).mean() * 100
    return out


# ---------------------------------------------------------------- 一键全套
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """给日线 DataFrame 一次性添加常用技术指标。"""
    out = df.copy()
    out = add_ma(out)
    out = add_ema(out)
    out = add_macd(out)
    out = add_rsi(out)
    out = add_kdj(out)
    out = add_boll(out)
    out = add_atr(out)
    out = add_wr(out)
    out = add_cci(out)
    out = add_obv(out)
    out = add_momentum(out)
    out = add_psy(out)
    return out
