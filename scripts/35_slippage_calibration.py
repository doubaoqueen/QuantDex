"""第 35 步：滑点校准 v1——用 5 分钟线测有效价差，回答 SLIP_RATE=0.0005 是否合理（D-067）。

SLIP_RATE 的理论构成：一半有效价差（吃单过价差）+ 冲击成本 + 时点漂移。
本项目单笔订单 ~2 万元（100 万/50 只），对日成交额千万级标的的冲击成本 ≈ 0；
时点漂移已由 S0/D-064（执行形态）与 D-066/S1（窗口成本）单独测量。
所以本脚本测**有效价差**：Corwin-Schultz (2012) 高低价估计器，作用于 5 分钟
bar 对（比日线 H-L 更细，且不受 A 股涨跌停挡板的长尾影响），逐标的取中位数。

产出：
  1) 每只股票的有效价差（bp）与半价差（=吃单成本）；
  2) 按价格档 × 流动性档的分层表——SLIP_RATE 该按什么数定；
  3) 与现行 SLIP_RATE=0.0005（5bp 单边）对照的结论。
口径：bar 级价差估计器有噪声（估计的是量级，不是精确值）；取中位数压噪。
用法：python scripts/35_slippage_calibration.py
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


def cs_spread_bp(h1, l1, h2, l2) -> np.ndarray:
    """Corwin-Schultz 两 bar 价差估计（bp）。负 alpha 记 0（理论下界）。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        b = np.log(h1 / l1) ** 2 + np.log(h2 / l2) ** 2
        g = (np.log(np.maximum(h1, h2) / np.minimum(l1, l2))) ** 2
        k = (3 - 2 * np.sqrt(2)) ** -1  # ≈ 3 + 2√2
        alpha = (np.sqrt(2 * b) - np.sqrt(b)) / (3 - 2 * np.sqrt(2)) - np.sqrt(g * k)
        alpha = np.where(alpha < 0, 0.0, alpha)
        s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return s * 10000.0  # bp


def symbol_spread(sym: str, fp: str) -> dict:
    """单标的：5 分钟 bar 对价差估计 → 中位（bp）；附带价格与流动性上下文。"""
    df = pd.read_parquet(fp, columns=["datetime", "high", "low", "close", "amount"])
    if len(df) < 200 or df["amount"].sum() <= 0:
        return {}
    df["day"] = df["datetime"].dt.date
    # 只在日内相邻 bar 之间估（跨午休/隔夜的 bar 对会污染估计）
    df = df.sort_values("datetime")
    gap_ok = df["datetime"].diff() == pd.Timedelta(minutes=5)
    h1, l1 = df["high"].shift(1), df["low"].shift(1)
    h2, l2 = df["high"], df["low"]
    valid = gap_ok & (h1 > 0) & (l1 > 0) & (l2 > 0)
    s = cs_spread_bp(h1.to_numpy(), l1.to_numpy(), h2.to_numpy(), l2.to_numpy())
    s = pd.Series(s)[valid.to_numpy()]
    s = s[np.isfinite(s) & (s >= 0)]
    if len(s) < 100:
        return {}
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    return {
        "symbol": sym,
        "spread_bp": float(s.median()),
        "spread_p25": float(s.quantile(0.25)),
        "spread_p75": float(s.quantile(0.75)),
        "price_med": float(close.median()),
        "amount_day_med": float(df.groupby("day")["amount"].sum().median()),
    }


def main() -> None:
    files = sorted(glob(str(MINUTE_DIR / "*__5__none.parquet")))
    if not files:
        print(">> 分钟数据不存在")
        sys.exit(2)
    print(f">> 滑点校准 v1（Corwin-Schultz @ 5min bar 对）：{len(files)} 只", flush=True)
    rows = []
    for i, fp in enumerate(files, 1):
        sym = fp.split("\\")[-1].split("__")[0]
        try:
            r = symbol_spread(sym, fp)
            if r:
                rows.append(r)
        except Exception as e:  # noqa: BLE001
            print(f"   [error] {sym}: {e}", flush=True)
        if i % 100 == 0 or i == len(files):
            print(f"   进度 {i}/{len(files)}", flush=True)
    df = pd.DataFrame(rows)
    half = df["spread_bp"] / 2.0

    print(f"\n== 有效价差（bp，{len(df)} 只）==")
    print(f"  中位 {df['spread_bp'].median():.1f} | P25 {df['spread_bp'].quantile(0.25):.1f} "
          f"| P75 {df['spread_bp'].quantile(0.75):.1f}")
    print(f"  半价差（吃单成本）中位 {half.median():.2f} bp")

    print("\n== 按价格档（收盘中位）==")
    df["price_q"] = pd.qcut(df["price_med"], 4,
                            labels=["低价股", "中低", "中高", "高价股"])
    print(df.groupby("price_q", observed=True)["spread_bp"].median().round(1).to_string())

    print("\n== 按流动性档（日成交额中位）==")
    df["liq_q"] = pd.qcut(df["amount_day_med"], 4,
                          labels=["Q1最低", "Q2", "Q3", "Q4最高"])
    print(df.groupby("liq_q", observed=True)["spread_bp"].median().round(1).to_string())

    # SLIP_RATE 对照：现行 5bp 单边
    print("\n== SLIP_RATE=0.0005（5bp 单边）对照 ==")
    for name, sub in [("全池", df),
                      ("低价+低流动（最差象限）", None)]:
        if sub is None:
            lowp = df["price_med"] <= df["price_med"].quantile(0.25)
            lowl = df["amount_day_med"] <= df["amount_day_med"].quantile(0.25)
            sub = df[lowp & lowl]
        hs = sub["spread_bp"].median() / 2.0
        print(f"  {name:<24} 中位半价差 {hs:.2f}bp → 现行 5bp "
              f"{'偏保守 ✓' if hs < 5 else '偏乐观 ✗（低估成本）'}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / "slippage_spread_by_symbol.parquet", index=False)
    try:
        record("slippage_calibration_v1",
               params={"symbols": int(len(df)),
                       "method": "Corwin-Schultz @ 5min bar pairs"},
               metrics={"spread_median_bp": float(df["spread_bp"].median()),
                        "half_spread_median_bp": float(half.median()),
                        "worst_quadrant_half_bp": float(
                            df[(df["price_med"] <= df["price_med"].quantile(0.25))
                               & (df["amount_day_med"] <= df["amount_day_med"].quantile(0.25))
                               ]["spread_bp"].median() / 2)},
               notes="SLIP_RATE=0.0005 校准：结论见 DECISIONS D-067",
               artifacts=["research_results/slippage_spread_by_symbol.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
