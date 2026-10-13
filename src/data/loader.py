import pandas as pd
from pathlib import Path

def load_csv(symbol: str = "510300", data_dir: str = "data/raw") -> pd.DataFrame:
    """加载本地CSV，做基础清洗和类型转换"""
    path = Path(data_dir) / f"{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {path}")

    df = pd.read_csv(path)

    # 类型转换
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 按日期排序，重置索引
    df = df.sort_values("date").reset_index(drop=True)

    # 只保留需要的列
    df = df[["date", "open", "high", "low", "close", "volume"]]

    return df


if __name__ == "__main__":
    df = load_csv("510300")
    print(df.head())
    print(df.dtypes)
    print(f"共 {len(df)} 行，{df['date'].min()} ~ {df['date'].max()}")