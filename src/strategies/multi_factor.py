# -*- coding: utf-8 -*-
"""
多因子合成策略：截面打分 -> 定期调仓的 Top-N 等权目标权重矩阵。

约定（与 src/backtest/portfolio.py 配套）：
- 输入 panel 为 build_factor_panel 输出（因子列已是每日截面 rank，0~1）；
- 输出 W：index=信号日（面板中的交易日，收盘 T），columns=symbols，
  值为目标权重（1/top_n，其余为现金）；
- 引擎负责 T+1 执行与费用扣减，策略只产生信号。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def momentum_lowvol_topn(
    panel: pd.DataFrame,
    top_n: int = 50,
    rebalance_days: int = 20,
    momentum: str = "mom_20",
    lowvol: str = "vol_20",
) -> pd.DataFrame:
    """20 日动量 + 低波动 等权合成，每 rebalance_days 个交易日调仓，取前 top_n 等权。

    score = 0.5 * rank(mom_20) + 0.5 * (1 - rank(vol_20))
    （动量越强、波动越低，得分越高；rank 已在面板中按日截面完成）
    """
    if momentum not in panel.columns or lowvol not in panel.columns:
        raise KeyError(f"面板缺少因子列 {momentum} / {lowvol}")
    p = panel[["date", "symbol", momentum, lowvol]].dropna().copy()
    p["score"] = 0.5 * p[momentum] + 0.5 * (1.0 - p[lowvol])

    dates = np.sort(p["date"].unique())
    reb_dates = dates[::max(1, rebalance_days)]
    rows: dict = {}
    for d in reb_dates:
        day = p[p["date"] == d].nlargest(top_n, "score")
        if day.empty:
            continue
        rows[pd.Timestamp(d)] = pd.Series(
            1.0 / len(day), index=day["symbol"].astype(str))
    W = pd.DataFrame(rows).T.fillna(0.0)
    W.index.name = "date"
    return W
