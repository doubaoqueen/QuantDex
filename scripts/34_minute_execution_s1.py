"""第 34 步：S1 执行窗口研究 v1（INTRADAY_EXECUTION_DESIGN §5/§8 S1，D-066）。

用已回填的 5 分钟线（586/949 只，代码序前 62%：000/002/300 + 早段 600，非随机
样本——科创板/晚段沪市缺席，v1 注明）测量：**在日内不同时点执行，相对当日
全日 VWAP 的成本曲线**。

定义：候选时点 w 的执行价 = 该 5 分钟 bar 的 VWAP（bar.amount/bar.volume——
每根 bar 本身就是 5 分钟段均价）；成本_w = bar_vwap / 当日全日 VWAP - 1。
买方视角：正数 = 比全天均价贵（吃亏），负数 = 便宜。涨跌停 bar（零成交）
无法成交，剔除。只保留 ≥40 根 bar 的完整交易日。

时点：设计文档 11 时点 + 开盘（9:30 集合竞价≈首根 bar open）+ 收盘（15:00 bar close）。
统计纪律（沿用 S0/D-064）：窗口成本的分年方向一致才可采纳；单看全期中位数不作为结论。

这个曲线回答两件事：
  1) T+1 开盘撮合是否在为"开盘溢价"付钱（若付，量化是多少 bp → 滑点校准原料）；
  2) 若将来研究收盘竞价执行（S2 close_auction），最便宜窗口在哪。
不回答"要不要换执行时点"——那是 S0 级别的口径决策，需影子盘 A/B。

用法：python scripts/34_minute_execution_s1.py
"""
import sys
from glob import glob

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record

MINUTE_DIR = STORE_DIR / "market" / "minute"
OUT_DIR = STORE_DIR / "research_results"
# 设计文档 11 时点（bar 的结束时刻）+ 9:30 开盘 + 15:00 收盘
WINDOWS = ["09:35", "09:45", "10:00", "10:30", "11:00", "13:05",
           "13:30", "14:00", "14:30", "14:45", "14:55", "15:00"]
MIN_BARS = 40


def daily_rows(sym: str, fp: str) -> pd.DataFrame:
    """单标的 → 逐日逐窗口成本行（含开盘 open 溢价、收盘 close）。"""
    df = pd.read_parquet(fp, columns=["datetime", "open", "close",
                                      "high", "low", "volume", "amount"])
    if df.empty:
        return pd.DataFrame()
    df["day"] = df["datetime"].dt.date
    df["hhmm"] = df["datetime"].dt.strftime("%H:%M")
    out = []
    for day, g in df.groupby("day"):
        if len(g) < MIN_BARS or g["amount"].sum() <= 0:
            continue
        vwap_day = g["amount"].sum() / g["volume"].sum()
        gv = g.set_index("hhmm")
        amt = gv["amount"]
        vol = gv["volume"]
        row = {"symbol": sym, "day": day, "year": pd.Timestamp(day).year,
               "amount_day": float(g["amount"].sum()), "n_bars": int(len(g))}
        for w in WINDOWS:
            if w == "09:35":
                # 开盘执行价 ≈ 首根 bar 的 open（集合竞价价）
                first = g.sort_values("datetime").iloc[0]
                px = float(first["open"])
                nv = 0  # 标记：这是 bar open 而非 bar vwap
            elif w == "15:00":
                last = g.sort_values("datetime").iloc[-1]
                px = float(last["close"])
                nv = 0
            else:
                if w not in gv.index or vol.get(w, 0) <= 0:
                    continue
                px = float(amt.get(w)) / float(vol.get(w))
                nv = 1
            row[f"c_{w}"] = px / vwap_day - 1.0
            row[f"nv_{w}"] = nv
        out.append(row)
    return pd.DataFrame(out)


def main() -> None:
    files = sorted(glob(str(MINUTE_DIR / "*__5__none.parquet")))
    if not files:
        print(">> 分钟数据不存在")
        sys.exit(2)
    print(f">> S1 执行窗口研究 v1：{len(files)} 只（回填中的部分样本，代码序前 62%）",
          flush=True)
    all_rows = []
    for i, fp in enumerate(files, 1):
        sym = fp.split("\\")[-1].split("__")[0]
        try:
            all_rows.append(daily_rows(sym, fp))
        except Exception as e:  # noqa: BLE001
            print(f"   [error] {sym}: {e}", flush=True)
        if i % 100 == 0 or i == len(files):
            print(f"   进度 {i}/{len(files)}", flush=True)
    big = pd.concat(all_rows, ignore_index=True)
    cost_cols = [c for c in big.columns if c.startswith("c_")]
    print(f"   有效标的日 {len(big):,} 个（{big['symbol'].nunique()} 只 × "
          f"{big['day'].nunique()} 日内）", flush=True)

    # ---- 全期成本曲线（中位数 + IQR）----
    med = big[cost_cols].median().sort_values()
    q25 = big[cost_cols].quantile(0.25)
    q75 = big[cost_cols].quantile(0.75)
    print("\n== 执行成本曲线（相对当日 VWAP，买方视角，bp）==")
    print(f"{'窗口':<8} {'中位':>8} {'P25':>8} {'P75':>8}")
    for c in cost_cols:
        w = c[2:]
        print(f"{w:<8} {med[c]*10000:>+8.1f} {q25[c]*10000:>+8.1f} {q75[c]*10000:>+8.1f}")

    # ---- 分年稳定性（统计纪律）----
    print("\n== 分年中位数（bp）——方向是否逐年一致 ==")
    yr = big.groupby("year")[cost_cols].median() * 10000
    print(yr.round(1).to_string())
    consistent = {c: bool((yr[c] > 0).all() or (yr[c] < 0).all()) for c in cost_cols}

    # ---- 流动性分层（按当日成交额五分位）----
    print("\n== 按当日成交额五分位的开盘溢价（c_09:35，bp 中位）==")
    big["liq_q"] = pd.qcut(big["amount_day"], 5,
                           labels=["Q1最低", "Q2", "Q3", "Q4", "Q5最高"])
    liq = big.groupby("liq_q", observed=True)["c_09:35"].median() * 10000
    print(liq.round(1).to_string())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    big.to_parquet(OUT_DIR / "s1_window_costs.parquet", index=False)
    pd.DataFrame({"window": cost_cols, "median_bp": med.values,
                  "p25_bp": q25.values, "p75_bp": q75.values,
                  "yearly_consistent": [consistent[c] for c in cost_cols]}
                  ).to_parquet(OUT_DIR / "s1_window_summary.parquet", index=False)

    try:
        record("s1_execution_windows",
               params={"symbols": int(big["symbol"].nunique()),
                       "symbol_days": int(len(big)), "sample": "前62%代码序"},
               metrics={c: float(med[c]) for c in cost_cols},
               notes="S1 v1：执行窗口成本曲线（相对日 VWAP，bp）；分年一致性见 summary",
               artifacts=["research_results/s1_window_summary.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
