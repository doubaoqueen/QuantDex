"""策略：双均线择时（快线在慢线上方持仓，否则空仓，信号 T+1 执行）。"""
import pandas as pd


def dual_ma_signal(
    df: pd.DataFrame,
    fast: int = 5,
    slow: int = 20,
    price_col: str = "close",
) -> pd.DataFrame:
    """
    输入标准化日线，输出含 ma_fast/ma_slow/raw_signal/position 的 DataFrame。
    raw_signal 为当日收盘信号，position 后移一日（T+1 开盘执行）。
    """
    if fast >= slow:
        raise ValueError("fast 必须小于 slow")
    out = df.copy()
    out["ma_fast"] = out[price_col].rolling(window=fast).mean()
    out["ma_slow"] = out[price_col].rolling(window=slow).mean()
    out["raw_signal"] = (out["ma_fast"] > out["ma_slow"]).astype(int)
    out["position"] = out["raw_signal"].shift(1).fillna(0).astype(int)
    return out


if __name__ == "__main__":
    from src.data.market import get_kline

    df = get_kline("510300")
    df = dual_ma_signal(df, fast=5, slow=20)
    print(df[["date", "close", "ma_fast", "ma_slow",
              "raw_signal", "position"]].tail(20))
