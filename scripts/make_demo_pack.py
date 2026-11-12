"""生成 demo 数据包（D-082）：从本地仓库导出小体积子集，供陌生人 30 秒出图。

用法：python scripts/make_demo_pack.py          # 覆盖式重建 demo/store/
产出：demo/store/market/daily/{code}__qfq.parquet ×5（近 2 年 qfq 日线）
      demo/store/meta/trade_calendar.parquet    （裁剪到同区间）
体积：约 0.5 MB，随仓库提交——这是 D-035「数据出 git」的唯一显式豁免。
数据来源：本机 data/store（东财/baostock 抓取），仅供演示，不构成投资建议。
"""
import _bootstrap  # noqa: F401

import pandas as pd

from config import BASE_DIR, STORE_DIR
from src.data.schema import kline_file_name
from src.data.storage import ParquetStore

# 常见大白马，覆盖沪/深主板：贵州茅台/五粮液/招商银行/美的集团/中国平安
SYMBOLS = ["600519", "000858", "600036", "000333", "601318"]
YEARS = 2
OUT_STORE = BASE_DIR / "demo" / "store"


def main() -> None:
    store = ParquetStore()
    start = pd.Timestamp.now().normalize() - pd.DateOffset(years=YEARS)
    n_rows = 0
    for sym in SYMBOLS:
        df = store.read("daily", kline_file_name(sym, "qfq"))
        df = df[df["date"] >= start].copy()
        if df.empty:
            raise SystemExit(f"[demo] {sym} 本地无近 {YEARS} 年数据，先跑 scripts/02_update_market.py")
        out = OUT_STORE / "market" / "daily"
        out.mkdir(parents=True, exist_ok=True)
        # kline_file_name 返回无扩展名 stem（ParquetStore 内部补 .parquet），这里直写需显式补
        df.to_parquet(out / f"{kline_file_name(sym, 'qfq')}.parquet", index=False)
        n_rows += len(df)
        print(f"  {sym}: {len(df)} 行（{df['date'].min().date()} ~ {df['date'].max().date()}）")

    # 交易日历裁剪到包区间（质检的缺日检查用它；缺了也能跑，只是跳过该检查）
    cal = pd.read_parquet(STORE_DIR / "meta" / "trade_calendar.parquet")
    cal = cal[pd.to_datetime(cal["date"]) >= start].copy()
    meta = OUT_STORE / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    cal.to_parquet(meta / "trade_calendar.parquet", index=False)
    print(f"  trade_calendar: {len(cal)} 天")

    print(f">> demo pack 完成：{OUT_STORE}（{len(SYMBOLS)} 只 / {n_rows} 行日线）")


if __name__ == "__main__":
    main()
