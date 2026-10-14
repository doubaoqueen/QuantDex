# -*- coding: utf-8 -*-
"""
因子库：从标准化日线构造截面因子面板。

概念
----
- 因子（Factor）= 给每个标的每天算一个「分数」的公式，用来解释/预测未来收益。
- 本文件先在单标的时序上算「原始因子值」，再在每个交易日的横截面上做 rank
  （百分位 0~1），消除量纲与极值。rank 之后：
      * 因子与未来收益做 Pearson 相关 = 业界常用的 RankIC；
      * 按因子 rank 分 5 组即分层回测。
- 因子「值大好还是小好」不预设，统一由检验出的 IC 符号揭示（IC<0 说明越小越好）。

第一版因子全部可由日线（OHLCV + 换手率）计算，无需财报对齐。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.schema import normalize_code, kline_file_name
from src.data.storage import ParquetStore

# 因子名与中文说明（方向以实测 IC 为准）
FACTOR_NAMES = [
    "mom_20",      # 动量
    "rev_5",       # 短期反转
    "mom_60",      # 中期动量
    "vol_20",      # 低波
    "turnover_20", # 换手/情绪
    "rsi_14",      # 超买超卖
    "ma60_bias",   # 均线偏离
    "amihud_20",   # 非流动性
]

FACTOR_DESC = {
    "mom_20": "过去20日收益率（动量：追涨杀跌）",
    "rev_5": "过去5日收益率（短期反转：跌多了反弹）",
    "mom_60": "过去60日收益率（中期动量）",
    "vol_20": "过去20日日收益标准差（低波因子，理论上越小越好）",
    "turnover_20": "过去20日平均换手率（情绪/投机度，理论上越冷越好）",
    "rsi_14": "14日RSI（>70超买、<30超卖）",
    "ma60_bias": "收盘价相对60日均线的偏离度（乖离，过大易回归）",
    "amihud_20": "Amihud非流动性 |收益|/成交额 的20日均值（越大越缺乏流动性）",
}


# ---------------------------------------------------------------- 单标的：原始因子
def compute_raw_factors(df: pd.DataFrame) -> pd.DataFrame:
    """在单标的时序上计算原始因子值（未做截面标准化）。"""
    out = df.sort_values("date").copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    amount = pd.to_numeric(out.get("amount"), errors="coerce")
    turnover = pd.to_numeric(out.get("turnover"), errors="coerce")

    out["ret"] = close.pct_change()

    # 动量 / 反转
    out["mom_20"] = close.pct_change(20)
    out["rev_5"] = close.pct_change(5)
    out["mom_60"] = close.pct_change(60)

    # 波动率
    out["vol_20"] = out["ret"].rolling(20).std()

    # 换手率
    out["turnover_20"] = turnover.rolling(20).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi_14"] = (100 - 100 / (1 + rs)).where(avg_loss != 0, 100)

    # 60 日均线乖离
    ma60 = close.rolling(60).mean()
    out["ma60_bias"] = close / ma60 - 1

    # Amihud 非流动性
    out["amihud_20"] = (
        out["ret"].abs() / amount.replace(0, np.nan)
    ).rolling(20).mean()

    return out


# ---------------------------------------------------------------- 截面因子面板
def build_factor_panel(
    store: ParquetStore,
    symbols: list[str],
    start: str | None = None,
    factor_names: list[str] | None = None,
    adjust: str = "qfq",
    pit_constituents: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    读取多标的日线 -> 算原始因子 -> 拼成长表 -> 每个交易日截面 rank(pct)。

    返回列：date, symbol, close, ret, 以及各因子的截面百分位(0~1)。
    adjust 指定读取哪套复权口径（默认 qfq，与 04 脚本写入口径一致）。

    pit_constituents: 传入 point-in-time 成分表（universe.load_pit_constituents 的输出）
    时，面板只保留「当天确实是该指数成分」的 (date, symbol) 行——消除幸存者偏差。
    判定口径：in_date <= date < out_date，out_date 为空视为至今仍是成分。
    """
    factor_names = factor_names or FACTOR_NAMES
    keep = ["date", "symbol", "close", "ret", *factor_names]
    frames = []

    for sym in symbols:
        code = normalize_code(sym)
        name = kline_file_name(code, adjust)
        if not store.exists("daily", name):
            continue
        df = store.read("daily", name)
        raw = compute_raw_factors(df)
        frames.append(raw[keep])

    if not frames:
        raise RuntimeError("没有可用日线数据，请先抓取行情。")

    panel = pd.concat(frames, ignore_index=True)
    if start:
        panel = panel[panel["date"] >= pd.Timestamp(start)]

    # point-in-time 过滤：只保留当时真实在指数内的 (date, symbol)
    if pit_constituents is not None:
        pit = pit_constituents[["symbol", "in_date", "out_date"]].copy()
        pit["in_date"] = pd.to_datetime(pit["in_date"])
        pit["out_date"] = pd.to_datetime(pit["out_date"])
        merged = panel.merge(pit, on="symbol", how="inner")
        keep_rows = (
            (merged["date"] >= merged["in_date"])
            & (merged["out_date"].isna() | (merged["date"] < merged["out_date"]))
        )
        panel = merged[keep_rows].drop(columns=["in_date", "out_date"])
        panel = panel.reset_index(drop=True)

    # 截面 rank：同一天所有股票横向排名为百分位
    for f in factor_names:
        panel[f] = panel.groupby("date")[f].rank(pct=True)

    panel = panel.sort_values(["date", "symbol"]).reset_index(drop=True)
    return panel
