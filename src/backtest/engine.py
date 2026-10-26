"""
向量化回测引擎（T+1 开盘成交，避免未来函数）：
- 信号 T 日收盘生成，T+1 日开盘按开盘价执行
- 双边手续费、滑点；卖出印花税（股票 0.05%，ETF 为 0）
- 输出净值、基准净值、回撤序列、逐笔交易明细
"""
import pandas as pd

from src.backtest.metrics import extract_trades


def run_backtest(
    df: pd.DataFrame,
    price_col: str = "open",
    fee: float = 0.00025,        # 券商佣金（单边）
    slippage: float = 0.0005,    # 滑点（双边，按成交额；ETF流动性好约万5）
    stamp_duty: float = 0.0,     # 卖出印花税，股票 0.0005，ETF 0
    transfer_fee: float = 0.00001,  # 过户费（双边，0.001%）
    init_cash: float = 100000.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (每日结果 DataFrame, 逐笔交易 DataFrame)。"""
    out = df.copy()

    # 当日开盘价相对前一开盘的收益（持仓在开盘成交后建立）
    out["ret"] = out[price_col].pct_change().fillna(0)

    # 策略毛收益 = 前一日仓位 * 当日收益
    out["strat_ret"] = out["position"].shift(1).fillna(0) * out["ret"]

    # 交易方向与成本
    pos_chg = out["position"].diff().fillna(out["position"])
    buys = (pos_chg > 0).astype(float)
    sells = (pos_chg < 0).astype(float)
    turnover = buys + sells
    out["turnover"] = turnover
    out["cost"] = (
        turnover * slippage
        + buys * fee
        + sells * (fee + stamp_duty)
        + turnover * transfer_fee
    )

    out["strat_ret_net"] = out["strat_ret"] - out["cost"]
    out["nav"] = (1 + out["strat_ret_net"]).cumprod()
    out["benchmark_nav"] = (1 + out["ret"]).cumprod()
    out["drawdown"] = out["nav"] / out["nav"].cummax() - 1
    out["init_cash_nav"] = init_cash * out["nav"]

    trades = extract_trades(out, price_col=price_col)
    return out, trades


if __name__ == "__main__":
    from src.backtest.metrics import performance
    from src.data.market import get_kline
    from src.strategies.dual_ma import dual_ma_signal

    df = get_kline("510300")
    df = dual_ma_signal(df, fast=5, slow=20)
    result, trades = run_backtest(df, stamp_duty=0.0)
    stats = performance(result, trades)
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"{k}: {v:.2%}")
        else:
            print(f"{k}: {v}")
