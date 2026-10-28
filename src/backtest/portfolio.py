"""
组合级回测引擎：目标权重矩阵 -> 组合净值（T+1 生效、含换手成本）。

约定（与单标的向量化引擎口径一致，无未来函数）：
- 输入 weights：index=信号日（收盘，T），columns=symbols，值=目标权重（Σ≤1，剩余为现金）；
- 信号在 T 收盘生成，**T+1 执行**（trade_lag=1）。两种执行价（D-034）：
  · open_prices=None（默认）：T+1 **收盘**执行——当日先按旧持仓吃满 t-1→t 收益，
    再在收盘按目标权重调仓并扣费（历史口径，向后兼容）；
  · open_prices 传入开盘价矩阵：T+1 **开盘**执行——与影子盘 pipeline 口径一致
    （旧持仓吃隔夜 open/prev_close，调仓在开盘成交，新持仓吃当日 open→close）；
  涨跌停判定随执行价：收盘口径用收盘涨跌幅（容差 limit_tol），开盘口径用
  开盘相对昨收的涨跌幅（容差 0.1%，与 pipeline 相同）；
- 收益：组合日收益 = Σ w_eff * r_close；两次调仓之间权重按个股收益自然漂移；
- 成本：逐笔佣金（含**最低佣金 5 元/笔**）+ 过户费 + 滑点 + 卖出印花税；
- 保真约束（limit_skip=True）：执行日**停牌**跳过该标的调仓；**涨停买不进/跌停卖不出**
  （按收盘涨跌幅判断，主板 10%、创业板/科创板 20%）→ 维持原权重；
- 分红：dividends=(date, symbol, dividend) 时，除权日按持仓权重计入现金（近似）。

用法：
    from src.backtest.portfolio import run_portfolio_backtest, portfolio_performance
    res = run_portfolio_backtest(W, prices, fee=2.5e-4, min_fee=5.0,
                                 slippage=5e-4, stamp_duty=5e-4,
                                 transfer_fee=1e-5, limit_skip=True,
                                 init_capital=1_000_000)
    portfolio_performance(res)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import board_limit

TRADING_DAYS = 250


def _board_limit(symbol: str) -> float:
    return board_limit(symbol)


def run_portfolio_backtest(
    weights: pd.DataFrame,
    prices: pd.DataFrame,
    fee: float = 0.00025,
    slippage: float = 0.0005,
    stamp_duty: float = 0.0,
    benchmark_returns: pd.Series | None = None,
    trade_lag: int = 1,
    min_fee: float = 0.0,
    transfer_fee: float = 0.0,
    limit_skip: bool = False,
    dividends: pd.DataFrame | None = None,
    init_capital: float = 1.0,
    verbose: bool = False,
    open_prices: pd.DataFrame | None = None,
    limit_tol: float = 0.002,
) -> dict:
    """目标权重矩阵 -> 组合回测结果。

    weights  : DataFrame（index=信号日，columns=symbols，值=目标权重，可含现金）
    prices   : DataFrame（index=交易日，columns=symbols，收盘价；缺失日 <= 0 视为无价格）
    """
    if weights.empty or prices.empty:
        raise ValueError("weights / prices 不能为空")

    cols = weights.columns.intersection(prices.columns)
    if len(cols) == 0:
        raise ValueError("weights 与 prices 没有共同标的")
    W = weights[cols].astype(float).fillna(0.0)

    # 原始价格（保留缺失，用于停牌判断）与 ffill 价格（用于收益）
    px_raw = prices[cols].sort_index()
    px = px_raw.ffill()
    dates = px.index
    rets = px.pct_change().fillna(0.0).replace([np.inf, -np.inf], 0.0)
    prev_raw = px_raw.shift(1)
    # 开盘执行模式（D-034）：隔夜 open/prev_close 与日内 close/open 两段收益
    exec_at_open = open_prices is not None
    if exec_at_open:
        op = open_prices[cols].sort_index().reindex(dates).ffill()
        ret_on = (op / px.shift(1) - 1.0).fillna(0.0).replace([np.inf, -np.inf], 0.0)
        ret_intra = (px / op - 1.0).fillna(0.0).replace([np.inf, -np.inf], 0.0)
        op_raw = open_prices[cols].sort_index().reindex(dates)

    # 分红：{日期: Series(symbol->每股分红)}
    div_by_date = None
    if dividends is not None and len(dividends):
        d = dividends.copy()
        d["date"] = pd.to_datetime(d["date"])
        div_by_date = {dd: g.groupby("symbol")["dividend"].sum().astype(float)
                       for dd, g in d.groupby("date")}

    # 信号日 -> 执行日（T 之后的第 trade_lag 个交易日）
    date_pos = {d: i for i, d in enumerate(dates)}
    eff = {}
    for d in W.index.sort_values():
        if d in date_pos:
            j = date_pos[d] + trade_lag
            if j < len(dates):
                eff[dates[j]] = d
        else:  # 信号日不在价格序列中：取之后最近的交易日
            later = dates[dates > d]
            if len(later) > trade_lag - 1:
                eff[later[trade_lag - 1]] = d
    if not eff:
        raise ValueError("没有任何信号日能映射到执行日（检查 weights 与 prices 的日期范围）")

    n_sym = len(cols)
    w_hold = np.zeros(n_sym)
    nav = 1.0
    nav_s, ret_s, turn_s = {dates[0]: 1.0}, {dates[0]: 0.0}, {}
    w_rounds = 0
    n_skipped = 0
    totals = {"commission": 0.0, "slippage": 0.0, "stamp_duty": 0.0, "transfer": 0.0}

    for i, t in enumerate(dates):
        # 除权日：现金分红（近似按持仓权重）
        if div_by_date is not None and t in div_by_date:
            dps = div_by_date[t]
            div_frac = 0.0
            for k, sym in enumerate(cols):
                dv = float(dps.get(sym, 0.0) or 0.0)
                if dv > 0 and w_hold[k] > 0:
                    p = float(px.loc[t, sym])
                    if p > 0:
                        div_frac += w_hold[k] * (dv / p)
            if div_frac:
                nav *= 1.0 + div_frac
                nav_s[t] = nav

        if i > 0 and (not exec_at_open or t not in eff):
            r = rets.iloc[i].to_numpy()
            port_r = float((w_hold * r).sum())
            nav *= 1.0 + port_r
            denom = 1.0 + port_r
            if denom > 1e-12:
                w_hold = w_hold * (1.0 + r) / denom
            nav_s[t] = nav
            ret_s[t] = port_r

        if t in eff and i > 0:
            w_target = W.loc[eff[t]].to_numpy().astype(float).copy()

            # 保真约束：停牌跳过、涨停买不进、跌停卖不出（D-034：随执行价切换判定基准）
            if limit_skip:
                raw_t = (op_raw.loc[t] if exec_at_open else px_raw.loc[t])
                raw_prev = prev_raw.loc[t]
                for k, sym in enumerate(cols):
                    if abs(w_target[k] - w_hold[k]) < 1e-12:
                        continue                          # 本就不需要调仓
                    if pd.isna(raw_t.get(sym)):
                        w_target[k] = w_hold[k]   # 无当日行情：无法交易，保持原仓位
                        if not pd.isna(raw_prev.get(sym)):
                            n_skipped += 1        # 昨日有价、今日无价 = 停牌
                        continue
                    p0 = raw_prev.get(sym)
                    if pd.isna(p0) or p0 <= 0:
                        continue
                    chg = float(raw_t.get(sym)) / float(p0) - 1.0
                    lim = _board_limit(sym)
                    tol = 0.001 if exec_at_open else limit_tol  # 开盘口径与 pipeline 相同
                    if w_target[k] > w_hold[k] and chg >= lim - tol:
                        w_target[k] = w_hold[k]           # 开盘/收盘触涨停：买不进
                        n_skipped += 1
                    elif w_target[k] < w_hold[k] and chg <= -lim + tol:
                        w_target[k] = w_hold[k]           # 开盘/收盘触跌停：卖不出
                        n_skipped += 1

            if exec_at_open and i > 0:
                # 隔夜：旧持仓 open_t / close_{t-1}；日内：新持仓 close_t / open_t
                r_on = ret_on.iloc[i].to_numpy()
                r_in = ret_intra.iloc[i].to_numpy()
                overnight = float((w_hold * r_on).sum())
                intra = float((w_target * r_in).sum())
                port_r = overnight + intra
                nav *= 1.0 + port_r
                denom = 1.0 + port_r
                if denom > 1e-12:
                    w_after = w_target * (1.0 + r_in) / denom
                else:
                    w_after = w_target
                nav_s[t] = nav
                ret_s[t] = port_r
                w_pre_cost = w_after          # 成本在下方统一扣减
            else:
                w_pre_cost = None

            delta = w_target - w_hold
            buy = np.maximum(delta, 0.0)
            sell = np.maximum(-delta, 0.0)
            notional = nav * init_capital
            buy_amt = buy * notional
            sell_amt = sell * notional
            comm = float(sum(max(a * fee, min_fee) for a in buy_amt if a > 0)
                         + sum(max(a * fee, min_fee) for a in sell_amt if a > 0))
            slip_amt = float((buy_amt.sum() + sell_amt.sum()) * slippage)
            stamp_amt = float(sell_amt.sum() * stamp_duty)
            trans_amt = float((buy_amt.sum() + sell_amt.sum()) * transfer_fee)
            cost_amt = comm + slip_amt + stamp_amt + trans_amt
            if notional > 0:
                nav *= 1.0 - cost_amt / notional
            totals["commission"] += comm
            totals["slippage"] += slip_amt
            totals["stamp_duty"] += stamp_amt
            totals["transfer"] += trans_amt
            if verbose:
                print(f"[{t.date()}] 换手 {buy.sum() + sell.sum():.3f} 成本 {cost_amt:,.2f}")
            nav_s[t] = nav
            turn_s[t] = buy.sum() + sell.sum()
            w_hold = w_after if (exec_at_open and w_pre_cost is not None) else w_target
            w_rounds += 1

    nav_series = pd.Series(nav_s).sort_index()
    ret_series = pd.Series(ret_s).sort_index()
    turn_series = pd.Series(turn_s).reindex(nav_series.index).fillna(0.0)

    result = {
        "nav": nav_series.astype(float),
        "daily_ret": ret_series.astype(float),
        "turnover": turn_series.astype(float),
        "n_rebalance": w_rounds,
        "n_skipped": n_skipped,
        "costs": {k: float(v) for k, v in totals.items()},
        "dates": nav_series.index,
    }
    result["costs"]["total"] = float(sum(totals.values()))
    if benchmark_returns is not None:
        br = benchmark_returns.reindex(nav_series.index).fillna(0.0)
        result["benchmark_nav"] = (1.0 + br).cumprod()
        result["benchmark_daily_ret"] = br.astype(float)
    result["drawdown"] = (nav_series / nav_series.cummax() - 1.0).astype(float)
    return result


def portfolio_performance(result: dict, rf: float = 0.018) -> dict:
    """组合绩效摘要：收益/风险/回撤/换手/超额/成本。"""
    nav = result["nav"]
    ret = result["daily_ret"]
    n = len(ret)
    total = float(nav.iloc[-1] - 1.0)
    ann = float(nav.iloc[-1] ** (TRADING_DAYS / n) - 1.0) if n else np.nan
    vol = float(ret.std() * np.sqrt(TRADING_DAYS))
    sharpe = (ann - rf) / vol if vol > 0 else np.nan
    dd = (nav / nav.cummax() - 1.0).min()
    calmar = ann / abs(dd) if dd < 0 else np.nan
    out = {
        "总收益率": total,
        "年化收益率": ann,
        "年化波动率": vol,
        "夏普比率": float(sharpe),
        "最大回撤": float(dd),
        "Calmar比率": float(calmar),
        "调仓次数": int(result.get("n_rebalance", 0)),
        "年均换手率": float(result["turnover"].sum() / max(n, 1) * TRADING_DAYS),
    }
    costs = result.get("costs")
    if costs:
        out["成本_佣金"] = costs.get("commission", np.nan)
        out["成本_印花税"] = costs.get("stamp_duty", np.nan)
        out["成本_过户费"] = costs.get("transfer", np.nan)
        out["成本_滑点"] = costs.get("slippage", np.nan)
        out["成本合计"] = costs.get("total", np.nan)
    if "benchmark_nav" in result:
        bnav = result["benchmark_nav"]
        btotal = float(bnav.iloc[-1] - 1.0)
        out["基准总收益率"] = btotal
        out["超额总收益"] = total - btotal
        exc = ret - result["benchmark_daily_ret"]
        exc_ann = float(exc.mean() * TRADING_DAYS)
        te = float(exc.std() * np.sqrt(TRADING_DAYS))
        out["年化超额收益"] = exc_ann
        out["信息比率"] = exc_ann / te if te > 0 else np.nan
    return out
