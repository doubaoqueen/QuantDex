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


def composite_topn(
    panel: pd.DataFrame,
    factors: list[tuple[str, int]],
    top_n: int = 50,
    rebalance_days: int = 20,
) -> pd.DataFrame:
    """多因子等权合成 Top-N 策略。

    factors: [(因子列名, 方向)]，方向 +1 表示"越大越好"，-1 表示"越小越好"。
    score = mean_i( rank(f_i) if dir=+1 else 1 - rank(f_i) )，rank 已按日截面完成。
    每 rebalance_days 个交易日调仓，选 score 最高 top_n 只等权。
    """
    cols = [f for f, _ in factors]
    missing = [c for c in cols if c not in panel.columns]
    if missing:
        raise KeyError(f"面板缺少因子列: {missing}")
    p = panel[["date", "symbol", *cols]].dropna().copy()
    score = None
    for f, direction in factors:
        r = p[f] if direction > 0 else (1.0 - p[f])
        score = r if score is None else score + r
    p["score"] = score / len(factors)

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


def turnover_lowvol_reversal_topn(
    panel: pd.DataFrame,
    top_n: int = 50,
    rebalance_days: int = 20,
) -> pd.DataFrame:
    """PIT 修正后的主线策略：低换手 + 低波 + 短期反转 等权合成。

    PIT 口径实测（2018-2026，564 只）：turnover_20 NW t=-4.61（最强）、
    vol_20 NW t=-4.47、rev_5 1日 NW t=-3.58——三者均为"越小越好"方向。
    """
    return composite_topn(
        panel,
        [("turnover_20", -1), ("vol_20", -1), ("rev_5", -1)],
        top_n=top_n, rebalance_days=rebalance_days)


def turnover_lowvol_dividend_topn(
    panel: pd.DataFrame,
    top_n: int = 50,
    rebalance_days: int = 20,
) -> pd.DataFrame:
    """红利版策略（2026-10-24 检验，D-027）：低换手 + 低波 + 股息率 等权合成。

    需要面板含 dy_ttm 列（build_factor_panel 传 dividend_panel 时生成）。
    检验结论（2018-2026 PIT + 保真回测 + walk-forward）：
      全周期 +74.15%/MDD -25.75%（现行 +30.35%/-29.88%）；
      样本外(2022-2026) +82.33%/MDD -13.17%（现行 +42.39%/-17.16%），年换手 702%。
    以前瞻验证为目的，以第三影子账户(div)运行，与现行策略并行对比。
    """
    return composite_topn(
        panel,
        [("turnover_20", -1), ("vol_20", -1), ("dy_ttm", +1)],
        top_n=top_n, rebalance_days=rebalance_days)


def _daily_score(panel: pd.DataFrame, factors: list[tuple[str, int]]) -> pd.DataFrame:
    """逐日截面打分（方向调整后 rank 均值），返回 date×symbol 的 score 宽表。"""
    cols = [f for f, _ in factors]
    missing = [c for c in cols if c not in panel.columns]
    if missing:
        raise KeyError(f"面板缺少因子列: {missing}")
    p = panel[["date", "symbol", *cols]].dropna().copy()
    score = None
    for f, direction in factors:
        r = p[f] if direction > 0 else (1.0 - p[f])
        score = r if score is None else score + r
    p["score"] = score / len(factors)
    return p.pivot(index="date", columns="symbol", values="score")


def topk_dropout_topn(
    panel: pd.DataFrame,
    factors: list[tuple[str, int]],
    topk: int = 50,
    n_drop: int = 5,
    rebalance_days: int = 20,
) -> pd.DataFrame:
    """TopkDropout 选股（对标 qlib TopkDropoutStrategy，D-031）。

    与 composite_topn 的区别：strict 重排名每次调仓把"跌出 topk"的全部卖出，
    边缘股票反复进出（年换手 1215% 的直接原因）；TopkDropout 每期**最多换出
    n_drop 只**——持仓∪候选合并按分数排序，卖出垫底 n_drop 中仍持有的
    （防"卖高买低"），买入同数量最高分新标的。换手上界 = n_drop/topk = 10%/期。

    qlib 原语义（signal_strategy.py L170-231，用户已对照源码验证）：
      today = 分数最高的 (n_drop + topk - len(held)) 只非持仓股
      comb  = 持仓 ∪ today，按分数降序
      sell  = 持仓 ∩ comb 垫底 n_drop
      buy   = today 前 (len(sell) + topk - len(held)) 只
    输出 W 矩阵（index=调仓日，1/len(held) 等权），供组合引擎 T+1 执行。
    """
    score_piv = _daily_score(panel, factors)
    dates = np.sort(score_piv.index.unique())
    reb_dates = dates[::max(1, rebalance_days)]
    held: list = []
    rows: dict = {}
    for d in reb_dates:
        if d not in score_piv.index:
            continue
        s = score_piv.loc[d].dropna().sort_values(ascending=False)
        if not held:
            held = list(s.index[:topk])
        else:
            held_valid = [x for x in held if x in s.index]
            n_slot = max(0, n_drop + topk - len(held_valid))
            today = [x for x in s.index if x not in held_valid][:n_slot]
            comb = s.reindex(held_valid + today).sort_values(ascending=False)
            sell_set = set(comb.index[-n_drop:]) if len(comb) > n_drop else set()
            sell = [x for x in held_valid if x in sell_set]
            buy = today[: len(sell) + topk - len(held_valid)]
            held = [x for x in held_valid if x not in set(sell)] + buy
        if held:
            rows[pd.Timestamp(d)] = pd.Series(1.0 / len(held), index=held)
    W = pd.DataFrame(rows).T.fillna(0.0)
    W.index.name = "date"
    return W


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
