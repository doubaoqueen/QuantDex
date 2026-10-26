"""完整绩效指标：收益/风险/回撤/交易质量/相对基准。"""
import numpy as np
import pandas as pd

from config import TRADING_DAYS_PER_YEAR


def extract_trades(df: pd.DataFrame, price_col: str = "open") -> pd.DataFrame:
    """
    从仓位变化提取完整交易（0->1 开仓，1->0 平仓），T+1 开盘成交。
    返回每笔 entry_date,exit_date,entry_price,exit_price,return,holding_days。
    """
    pos = df["position"].values
    price = df[price_col].values
    dates = pd.to_datetime(df["date"]).values
    trades = []
    entry_i = None
    for i in range(1, len(df)):
        if pos[i] == 1 and pos[i - 1] == 0:
            entry_i = i
        elif pos[i] == 0 and pos[i - 1] == 1 and entry_i is not None:
            ep, xp = price[entry_i], price[i]
            trades.append({
                "entry_date": pd.Timestamp(dates[entry_i]),
                "exit_date": pd.Timestamp(dates[i]),
                "entry_price": ep,
                "exit_price": xp,
                "return": xp / ep - 1 if ep else np.nan,
                "holding_days": i - entry_i,
            })
            entry_i = None
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

    # Sortino：仅用下行波动
    downside = ret[ret < 0]
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
