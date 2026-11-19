"""完整绩效指标：收益/风险/回撤/交易质量/相对基准。"""
import numpy as np
import pandas as pd

from config import TRADING_DAYS_PER_YEAR


def extract_trades(df: pd.DataFrame, price_col: str = "open") -> pd.DataFrame:
    """
    从仓位变化提取完整交易，T+1 开盘成交。返回每笔
    entry_date,exit_date,entry_price,exit_price,return,holding_days,avg_position。

    D-097：支持**分数仓位**（旧版硬假设 position ∈ {0,1}，0.02/0.5 之类的权重
    交易记录直接丢失）。语义：
    - entry：仓位从 ~0 变为正；
    - exit：仓位从正变回 ~0；
    - 持有期内加仓 → 入场价按金额加权更新；部分减仓 → 该段记录为一笔交易
      （入场价用当时的加权入场价），剩余仓位作为新的一段继续跟踪。
    二元仓位（0/1）下行为与旧版完全一致。
    """
    pos = df["position"].values
    price = df[price_col].values
    dates = pd.to_datetime(df["date"]).values
    eps = 1e-9
    trades = []
    in_pos = False
    entry_i = None
    entry_cost = 0.0     # Σ price×Δpos（加权入场成本）
    entry_qty = 0.0      # Σ |Δpos|（累计建仓量）
    qty = 0.0            # 当前仓位
    for i in range(len(df)):
        w_i, p_i = pos[i], price[i]
        dw = w_i - qty                                   # 当日仓位变动（首日=初始建仓）
        if not in_pos and w_i > eps:
            in_pos, entry_i = True, i
            entry_cost, entry_qty, qty = w_i * p_i, w_i, w_i
            continue
        if in_pos and abs(dw) > eps:
            if dw > 0:                                   # 加仓
                entry_cost += p_i * dw
                entry_qty += dw
                qty = w_i
            else:                                        # 减仓
                sell_qty = min(-dw, qty)
                ep = entry_cost / entry_qty if entry_qty > 0 else p_i
                trades.append({
                    "entry_date": pd.Timestamp(dates[entry_i]),
                    "exit_date": pd.Timestamp(dates[i]),
                    "entry_price": ep,
                    "exit_price": p_i,
                    "return": p_i / ep - 1 if ep else np.nan,
                    "holding_days": i - entry_i,
                    "avg_position": entry_qty,
                    "closed_qty": sell_qty,
                })
                entry_cost -= ep * sell_qty
                entry_qty -= sell_qty
                qty = w_i
                if qty <= eps:                           # 清仓完毕
                    in_pos = False
                    entry_i = None
                    entry_cost, entry_qty = 0.0, 0.0
                else:                                    # 部分减仓 → 余量新起一段
                    entry_i = i
                    entry_qty = qty
                    entry_cost = ep * qty
    return pd.DataFrame(trades)


def performance(df: pd.DataFrame, trades: pd.DataFrame | None = None,
                 rf: float = 0.018) -> dict:
    """计算完整绩效指标（df 为 engine 输出，含 nav/strat_ret_net/ret）。"""
    nav = df["nav"]
    ret = df["strat_ret_net"]
    n = len(df)

    total_return = nav.iloc[-1] - 1
    ann_return = (1 + total_return) ** (TRADING_DAYS_PER_YEAR / n) - 1
    vol = ret.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (ann_return - rf) / vol if vol > 0 else np.nan

    # Sortino：下行偏差用**全样本**口径（min(ret−rf,0)² 对全部 N 天取均值）——
    # D-097：旧实现只对下跌日取均值，分母被系统性**压低**（×√(下跌日/总日)<1），
    # Sortino 因此虚高（上涨日占比越高越夸张），属自创口径。修正后与标准 Sortino
    # 一致——数值会比旧版**高**，属口径修正，不是策略改善，勿解读为 alpha。
    downside = np.minimum(ret - rf, 0.0)
    downside_vol = np.sqrt((downside ** 2).mean()) * np.sqrt(TRADING_DAYS_PER_YEAR)
    sortino = (ann_return - rf) / downside_vol if downside_vol > 0 else np.nan

    # 最大回撤及其持续时间
    cummax = nav.cummax()
    dd = nav / cummax - 1
    max_dd = dd.min()
    calmar = ann_return / abs(max_dd) if max_dd < 0 else np.nan
    dd_end = dd.idxmin()
    dd_start = nav.iloc[:dd_end + 1].idxmax()
    # 回撤恢复日
    recovered = nav.iloc[dd_end:][nav.iloc[dd_end:] >= nav.iloc[dd_start]]
    dd_days = (recovered.index[0] - dd_start) if len(recovered) else np.nan

    result = {
        "总收益率": total_return,
        "年化收益率": ann_return,
        "年化波动率": vol,
        "夏普比率": sharpe,
        "Sortino比率": sortino,
        "最大回撤": max_dd,
        "Calmar比率": calmar,
        "最大回撤持续(交易日)": dd_days if pd.isna(dd_days) else int(dd_days),
    }

    # 交易质量
    if trades is None:
        trades = extract_trades(df)
    if len(trades):
        wins = trades[trades["return"] > 0]
        losses = trades[trades["return"] <= 0]
        avg_win = wins["return"].mean() if len(wins) else np.nan
        avg_loss = losses["return"].mean() if len(losses) else np.nan
        result.update({
            "交易笔数": len(trades),
            "胜率": len(wins) / len(trades),
            "平均每笔收益": trades["return"].mean(),
            "平均盈利": avg_win,
            "平均亏损": avg_loss,
            "盈亏比": abs(avg_win / avg_loss) if avg_loss and not np.isnan(avg_loss) and avg_loss != 0 else np.nan,
            "平均持仓天数": trades["holding_days"].mean(),
        })

    # 相对基准
    if "benchmark_nav" in df.columns:
        bench_total = df["benchmark_nav"].iloc[-1] - 1
        result["基准总收益率"] = bench_total
        result["超额总收益"] = total_return - bench_total
        excess_daily = ret - df["ret"]
        te = excess_daily.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
        ann_excess = excess_daily.mean() * TRADING_DAYS_PER_YEAR
        result["年化超额收益"] = ann_excess
        result["信息比率"] = ann_excess / te if te > 0 else np.nan

    return result
