"""第 52 步：D-091 进攻复活条件复查（预注册工具，每月数据补齐后运行一次）。

预注册条件（D-091，2026-11-18）：RS60（相对强度 = 个股 60 日收益 − 指数 60 日收益）
的**滚动 60 个 IC 日均值 ≥ +0.03 且 NW-t ≥ 2** 再次出现 → 进攻型闸门重新打开
（人工复核后走条件化检验：RS60 在阴跌 regime 的历史表现 + 影子盘第五账户）。

2026-11-20 首次实测（数据至 10-16——20 日前瞻窗的固有延迟，11 月 IC 要 12 月中补齐）：
  滚动 60 日均值 +0.157、NW-t +1.57 → **未触发**（差 0.43 个 t 单位）。
  趋势：滚动均值 +0.110 → +0.157 单调上升；分年 IC 多数为负（A 股反转结构），
  近期转正**与历史基率相反**——这是"待证"不是"已证"。
  阴跌段分层（回撤≥10% 定义覆盖 85% 天数）IC 反而 -0.034 → 该 regime 定义无效，
  条件化闸门需要更严格的 regime 定义（如 20 日市场动量符号），未定前不开门。

用法：python scripts/52_rs60_revival_check.py     # 输出条件是否触发 + 趋势
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factor_test import _newey_west_tstat
from src.data.storage import ParquetStore

AGG = STORE_DIR / "research_results" / "minute_ckpt_agg.parquet"
COND_MEAN, COND_T = 0.03, 2.0
ROLL_WINDOW = 60


def main() -> int:
    s = ParquetStore()
    agg = pd.read_parquet(AGG)
    pit = pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000300.parquet")
    pit["in_date"] = pd.to_datetime(pit["in_date"])
    pit["out_date"] = pd.to_datetime(pit["out_date"])
    p = agg.merge(pit, on="symbol", how="inner")
    p["date"] = pd.to_datetime(p["date"])
    p = p[(p["date"] >= p["in_date"])
          & (p["out_date"].isna() | (p["date"] < p["out_date"]))]
    close = p.pivot(index="date", columns="symbol", values="day_close").sort_index()

    idx = s.read("daily", "000300__none", columns=["date", "close"])
    idx["date"] = pd.to_datetime(idx["date"])
    bc = idx.set_index("date")["close"].sort_index().reindex(close.index).ffill()

    # 注意：DataFrame - Series 会把 Series 的日期索引对齐到**列**（股票代码）→ 全 NaN
    # （2026-11-20 踩坑）。必须 axis=0 按行减。
    rs60 = close.pct_change(60).sub(bc.pct_change(60), axis=0)
    fwd = close.shift(-21) / close.shift(-1) - 1.0

    ics = []
    for d in close.index[61:-21]:
        r = rs60.loc[d].dropna()
        f = fwd.loc[d].dropna()
        common_c = r.index.intersection(f.index)
        if len(common_c) < 30:
            continue
        rr, ff = r[common_c].rank(), f[common_c].rank()
        ic = ((rr - rr.mean()) * (ff - ff.mean())).sum() / np.sqrt(
            ((rr - rr.mean()) ** 2).sum() * ((ff - ff.mean()) ** 2).sum())
        ics.append((d, ic))
    ic_s = pd.Series(dict(ics)).dropna()
    last60 = ic_s.iloc[-ROLL_WINDOW:]
    mean60 = float(last60.mean())
    t60 = float(_newey_west_tstat(last60, 19))
    roll = ic_s.rolling(ROLL_WINDOW).mean().dropna()

    triggered = mean60 >= COND_MEAN and t60 >= COND_T
    print(f"数据至 {ic_s.index[-1].date()}（前瞻窗延迟：{ic_s.index[-1].strftime('%Y-%m')} 月的 IC"
          f" 要 { (ic_s.index[-1] + pd.Timedelta(days=35)).strftime('%Y-%m') } 中旬才补齐）")
    print(f"复活条件实测：最近 {ROLL_WINDOW} 个 IC 日均值 {mean60:+.4f} | NW-t {t60:+.2f} | "
          f"预注册阈值 {COND_MEAN}/{COND_T} → {'✅ 触发' if triggered else '❌ 未触发'}")
    print(f"滚动均值近 5 读数: {[f'{v:+.3f}' for v in roll.iloc[-5:]]}")
    yr = ic_s.groupby(ic_s.index.year).mean()
    print("分年 IC:", {str(y): f"{v:+.3f}" for y, v in yr.items()})
    if triggered:
        print("→ 复核流程：条件化闸门（RS60 × 阴跌 regime 历史检验）→ 影子盘第五账户提案")
    else:
        print("→ 门未开：进攻账户不建；下次复查 = 最新月份 IC 补齐后（约每月中旬）")
    return 0 if triggered else 1


if __name__ == "__main__":
    sys.exit(main())
