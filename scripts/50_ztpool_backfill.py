"""第 50 步：东财涨停池回填（D-093 后续假设的研究原料，2026-11-19）。

背景：D-093 用 5 分钟 bar 近似了封板/炸板，但缺**封板资金/封板时间/炸板次数**——
D-093 量出的信息墙（封单强度不可观测）的合法踏板。东财涨停池接口
（ak.stock_zt_pool_em，与本项目既有东财源同级）恰好提供这些字段，但
**历史保留期仅约一个月（10-20 之前失忆，实测）**——过期即失，必须滚动采集。

用法：
    python scripts/50_ztpool_backfill.py            # 回填保留窗口内全部交易日（幂等）
    python scripts/50_ztpool_backfill.py --days 5   # 只补最近 5 个交易日
落盘：data/store/market/ztpool/ztpool.parquet（date+code 主键，重跑覆盖去重）。
入链建议：10_daily_run 邮件后段每日调用一次（TODO 工程债）。
"""
import sys
import time
from datetime import datetime

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.sources import ak
from src.data.storage import ParquetStore

OUT = STORE_DIR / "market" / "ztpool" / "ztpool.parquet"
RETENTION_DAYS = 26        # 保留期约 22 个交易日，取 26 个日历日冗余
COLS = ["代码", "名称", "涨跌幅", "最新价", "成交额", "流通市值", "换手率",
        "封板资金", "首次封板时间", "最后封板时间", "炸板次数", "涨停统计",
        "连板数", "所属行业"]


def trading_days_back(store: ParquetStore, days: int) -> list[str]:
    cal = pd.to_datetime(pd.read_parquet(
        STORE_DIR / "meta" / "trade_calendar.parquet", columns=["date"])["date"]).dt.strftime("%Y%m%d")
    today = pd.Timestamp.now().strftime("%Y%m%d")
    past = [d for d in cal if d <= today]
    return past[-days:]


def main() -> None:
    store = ParquetStore()
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else RETENTION_DAYS
    targets = trading_days_back(store, days)
    today = pd.Timestamp.now().strftime("%Y%m%d")
    if datetime.now().time() < pd.Timestamp("15:05").time():
        # 盘前/盘中查"今天"，接口吐的是上一交易日的池子（占位数据）——跳过，收盘后补
        if today in targets:
            targets = [d for d in targets if d != today]
            print(f">> 盘中跳过今日（{today}）——收盘后 15:05 再补（盘前查今天=昨天占位数据）")
    print(f">> 涨停池回填：最近 {len(targets)} 个交易日（保留期约 1 个月，过期即失）")

    frames = []
    for d in targets:
        try:
            df = ak.stock_zt_pool_em(date=d)
        except Exception as e:  # noqa: BLE001  单日失败不拖垮批次（休市日接口也可能抛）
            print(f"   {d}: ✗ {str(e)[:50]}")
            continue
        if df is None or len(df) == 0:
            print(f"   {d}: 0 只（休市或超保留期）")
            continue
        keep = [c for c in COLS if c in df.columns]
        sub = df[keep].copy()
        sub.insert(0, "date", pd.to_datetime(d))
        frames.append(sub)
        print(f"   {d}: {len(df)} 只", flush=True)
        time.sleep(0.3)

    if not frames:
        print(">> 无新数据")
        return
    new = pd.concat(frames, ignore_index=True)
    if OUT.exists():
        old = pd.read_parquet(OUT)
        new = pd.concat([old[~old["date"].isin(new["date"])], new], ignore_index=True)
    new = new.drop_duplicates(subset=["date", "代码"], keep="last")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(OUT, index=False)
    n_days = new["date"].nunique()
    print(f"\n>> 落盘 {len(new)} 行 × {n_days} 个交易日 → {OUT}")
    print(">> D-093 后续假设（封板强度→次日）的原料开始积累；每日采集入链见 TODO 工程债")


if __name__ == "__main__":
    main()
