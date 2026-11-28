"""
小预算逐期整手回放：给定一笔钱，按策略真实的 20 日调仓序列模拟"这个账户会长什么样"。

为什么需要它（D-118 遗留问题，用户 2026-11-26 指认）：
  开户前体检的组合核查用的是**当日因子截面**（candidates 前 15 名）+ 等权预算假设，
  而策略实际是每 20 个交易日重排、买 50 只。两者有两处不一致：
    ① 当日截面 ≠ 调仓日的持仓（实测 D-072 重合度仅 34%）；
    ② k 只 ≠ 50 只——同权重按前 k 只截断，k=15 的 MDD -38.9%、k=10 -51.6%，
       而 50 只是 -31.9%（体检正是拿这后一个数当分母算"可投入"）。
  本模块把"预算"落到真实的整手/最低佣金/现金约束上，逐期回放，输出这个预算
  自己会经历的净值、回撤、成本率与持仓数——体检的分母应当来自这里，而不是
  从"你复制不了的 50 只组合"借。

口径（与 src/backtest/portfolio.py 的 T+1 开盘执行对齐）：
  · 信号日收盘出权重，次日开盘执行（trade_lag=1）；
  · 隔夜：旧持仓按 open_t / close_{t-1} 变动；日内：新持仓按 close_t / open_t；
  · 目标股数 = floor(目标权重 × 期初净值 / (开盘价 × 100)) × 100——买不起一手的直接落空；
  · 先卖后买（A 股卖出资金当日可用）；买入合计超现金时按比例整手缩单；
  · 成本：佣金（含最低 5 元/笔）+ 过户费 + 滑点 + 卖出印花税；statutory_costs=True
    时印花税/过户费按成交日法定费率（D-118）。
  不做涨跌停/停牌拒单（小预算的主要约束是整手与最低佣金，这两项对结论影响更大；
  需要时由调用方在权重层处理）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import LOT, STAMP_RATE_HISTORY, TRANSFER_RATE_HISTORY, rate_on

TRADING_DAYS = 250


def _costs(amount: float, side: str, fee: float, min_fee: float, slippage: float,
           stamp: float, transfer: float) -> tuple[float, dict]:
    """单笔成本，返回 (合计, 分项)。卖出才收印花税。"""
    comm = max(amount * fee, min_fee) if amount > 0 else 0.0
    slip = amount * slippage
    tr = amount * transfer
    st = amount * stamp if side == "sell" else 0.0
    return comm + slip + tr + st, {"commission": comm, "slippage": slip,
                                   "transfer": tr, "stamp_duty": st}


def run_mini_account(weights: pd.DataFrame, prices: pd.DataFrame, opens: pd.DataFrame,
                     capital: float, *, fee: float = 0.00025, min_fee: float = 5.0,
                     slippage: float = 0.0005, stamp_duty: float = 0.0005,
                     transfer_fee: float = 0.00001, statutory_costs: bool = True,
                     lot: int = LOT, trade_lag: int = 1) -> dict:
    """按预算逐期整手回放策略的调仓序列，返回这个预算的真实路径。

    返回 dict：
      nav / daily_ret / dates        每日净值与收益
      n_positions                    每日实际持仓只数（Series）
      n_skipped                      因"买不起一手"被落空的标的次数
      invested_ratio                 每日实际仓位（持仓市值 / 净值）
      costs                          成本分项累计（元）
      turnover_value                 累计成交额（元）
      n_rebalance                    执行日数
    """
    cols = weights.columns.intersection(prices.columns)
    if len(cols) == 0:
        raise ValueError("weights 与 prices 没有共同标的")
    W = weights[cols].astype(float).fillna(0.0)
    px_raw = prices[cols].sort_index()
    # 全程无价格的标的无法交易（退市/从未上市）——剔除，否则 ffill 后仍是 NaN
    cols = [c for c in cols if px_raw[c].notna().any()]
    if not cols:
        raise ValueError("prices 里没有任何可交易标的")
    W, px_raw = W[cols], px_raw[cols]
    px = px_raw.ffill()
    op = opens[cols].sort_index().reindex(px.index).ffill()
    dates = px.index
    op_v = op.to_numpy(float)
    cl_v = px.to_numpy(float)

    # 信号日 → 执行日（T 之后第 trade_lag 个交易日）
    pos_of = {d: i for i, d in enumerate(dates)}
    eff: dict = {}
    for d in W.index:
        if d in pos_of:
            j = pos_of[d] + trade_lag
            if j < len(dates):
                eff[j] = d

    n = len(cols)
    cash = float(capital)
    sh = np.zeros(n)
    nav_s: dict = {dates[0]: float(capital)}
    pos_s: dict = {}
    inv_s: dict = {}
    totals = {"commission": 0.0, "slippage": 0.0, "stamp_duty": 0.0, "transfer": 0.0}
    turnover_value = 0.0
    n_skipped = 0
    n_reb = 0

    for i in range(1, len(dates)):
        o, c = op_v[i], cl_v[i]
        # 价格缺失按 0 处理：`(0 股 × NaN 价).sum()` 会得到 NaN，空仓时把整条净值链污染
        # （2026-11-27 实测：前 62 个调仓日全部因此静默跳过），而 sh=0 的标的市值本就是 0
        o_v = np.nan_to_num(o, nan=0.0)
        c_v = np.nan_to_num(c, nan=0.0)
        # 1) 隔夜：旧持仓按 open/prev_close 变动
        mv_open = float((sh * o_v).sum())
        nav_open = cash + mv_open
        # 2) 执行（T+1 开盘）
        if i in eff and nav_open > 0:
            w_target = W.loc[eff[i]].to_numpy(float)
            with np.errstate(divide="ignore", invalid="ignore"):
                raw_tgt = np.where(o_v > 0, w_target * nav_open / (o_v * lot), 0.0)
            tgt = np.floor(raw_tgt) * lot
            tgt = np.where(np.isfinite(tgt), tgt, 0.0)
            n_skipped += int(((w_target > 0) & (tgt == 0)).sum())
            stamp_t = rate_on(STAMP_RATE_HISTORY, dates[i]) if statutory_costs else stamp_duty
            trans_t = rate_on(TRANSFER_RATE_HISTORY, dates[i]) if statutory_costs else transfer_fee
            # 先卖
            delta = tgt - sh
            for k in np.flatnonzero(delta < 0):
                qty = -delta[k]
                amt = qty * o_v[k]
                cost, parts = _costs(amt, "sell", fee, min_fee, slippage, stamp_t, trans_t)
                cash += amt - cost
                turnover_value += amt
                for kk, vv in parts.items():
                    totals[kk] += vv
                sh[k] += delta[k]
            # 后买：现金不足按比例整手缩单
            buys = np.flatnonzero(delta > 0)
            if len(buys):
                need = 0.0
                for k in buys:
                    amt = delta[k] * o[k]
                    cst, _ = _costs(amt, "buy", fee, min_fee, slippage, stamp_t, trans_t)
                    need += amt + cst
                scale = min(1.0, cash / need) if need > 0 else 1.0
                for k in buys:
                    qty = np.floor(delta[k] * scale / lot) * lot
                    if qty <= 0:
                        n_skipped += 1
                        continue
                    amt = qty * o_v[k]
                    cost, parts = _costs(amt, "buy", fee, min_fee, slippage, stamp_t, trans_t)
                    if amt + cost > cash + 1e-9:      # 缩单后仍差一点：再降一手
                        qty -= lot
                        if qty <= 0:
                            n_skipped += 1
                            continue
                        amt = qty * o_v[k]
                        cost, parts = _costs(amt, "buy", fee, min_fee, slippage,
                                             stamp_t, trans_t)
                    cash -= amt + cost
                    turnover_value += amt
                    for kk, vv in parts.items():
                        totals[kk] += vv
                    sh[k] += qty
            n_reb += 1
        # 3) 收盘估值：**股数不变**，按收盘价重估（股数只在交易时变化——早先版本
        #    把"按日内收益调整后的等效股数"写回了 sh，股数被价格污染、逐日累积偏差）
        mv_close = float((sh * c_v).sum())
        nav_close = cash + mv_close
        nav_s[dates[i]] = nav_close
        pos_s[dates[i]] = int((sh > 0).sum())
        inv_s[dates[i]] = mv_close / nav_close if nav_close > 0 else 0.0

    nav = pd.Series(nav_s).sort_index().astype(float)
    ret = nav.pct_change().fillna(0.0)
    yrs = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    cost_total = float(sum(totals.values()))
    return {
        "nav": nav,
        "daily_ret": ret,
        "dates": nav.index,
        "n_positions": pd.Series(pos_s).sort_index().astype(float),
        "invested_ratio": pd.Series(inv_s).sort_index().astype(float),
        "n_skipped": int(n_skipped),
        "n_rebalance": int(n_reb),
        "costs": {k: float(v) for k, v in totals.items()} | {"total": cost_total},
        "turnover_value": float(turnover_value),
        "annual_cost_pct": cost_total / capital / yrs,      # 年均成本占本金
        "annual_turnover_x": turnover_value / capital / yrs,
    }


def summarize(nav: pd.Series, bench_ret: pd.Series | None = None) -> dict:
    """净值序列的标准指标（年化/MDD/波动/IR），供分段与全样本共用。"""
    nav = nav.dropna()
    if len(nav) < 2:
        return {}
    yrs = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    ret = nav.pct_change().dropna()
    out = {
        "ann": float((nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1),
        "total": float(nav.iloc[-1] / nav.iloc[0] - 1),
        "mdd": float((nav / nav.cummax() - 1).min()),
        "vol": float(ret.std() * np.sqrt(TRADING_DAYS)),
        "days": int(len(nav)),
    }
    if bench_ret is not None and len(bench_ret):
        e = ret - bench_ret.reindex(ret.index).fillna(0.0)
        out["ir"] = float(e.mean() / e.std() * np.sqrt(TRADING_DAYS)) if e.std() > 0 else np.nan
    return out
