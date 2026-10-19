# -*- coding: utf-8 -*-
"""
组合级风控：
- 单票仓位上限（信号说满仓一只 -> 压到上限）；
- 总仓位上限（超出转现金，不重新分配）；
- 回撤断路器（回撤超过阈值 -> 仓位乘以缩放系数）；
- apply_risk 对目标权重矩阵逐日生效，可直接接在策略与回测/账本之间。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def cap_weights(weights: pd.Series, max_single: float = 0.10,
                max_total: float = 1.0) -> pd.Series:
    """单票截断 + 总仓位约束；超出部分转现金（不重新分配给其他票）。"""
    w = pd.to_numeric(weights, errors="coerce").fillna(0.0)
    w = w.clip(lower=0.0, upper=max_single)
    total = float(w.sum())
    if total > max_total:
        w = w / total * max_total
    return w


def drawdown_multiplier(nav: pd.Series, max_dd: float = 0.15,
                        scale: float = 0.5) -> pd.Series:
    """回撤断路器：当前回撤深于 max_dd（如 -15%）时，仓位乘 scale（如 0.5）。"""
    dd = nav / nav.cummax() - 1.0
    return pd.Series(np.where(dd < max_dd, scale, 1.0), index=nav.index)


def apply_risk(weights: pd.DataFrame, nav: pd.Series | None = None,
               max_single: float = 0.10, max_total: float = 1.0,
               max_dd: float = 0.15, dd_scale: float = 0.5) -> pd.DataFrame:
    """对目标权重矩阵（index=信号日，columns=symbols）逐日施加风控。"""
    out = weights.astype(float).copy()
    out = out.apply(lambda s: cap_weights(s, max_single, max_total), axis=1)
    if nav is not None:
        dd = (nav / nav.cummax() - 1.0).reindex(out.index)
        bad = dd < max_dd
        out.loc[bad, :] = out.loc[bad, :] * dd_scale
    return out
