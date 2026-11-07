"""因子月度监控（D-062）：滚动 IC 衰减预警 + 相关性聚类。

背景（2026-11-07 审计）：连在用的 4 个因子都没有失效告警，更谈不上激活闲置因子；
TODO P3"因子失效监控（月度滚动 IC 预警）"本脚本落地。

三个产出：
  1) 滚动 IC：55 个因子近 60 日 RankIC(h=5/20) 均值 vs 全样本均值，
     衰减判据：|近期| < 30%×|全样本| 或方向翻转（仅对全样本 |IC20|≥0.01 的因子判定）；
  2) 在用因子预警：corrected/dividend/topkdrop 在用的 4 因子任一衰减 → exit 1
     （周六调度可见，10_daily_run 转化为退出码 5）；
  3) 相关性聚类：每 5 日采样的截面 rank 相关矩阵，|corr|>0.7 因子对 + 每因子
     平均 |corr|——"到底有几个独立赌注"的量化（审计指出 8 因子只有 2~3 个独立赌注）。

数据：research_results/rotation_panel_all55.parquet（30 号脚本的缓存面板；
      不存在时提示先跑 `python scripts/30_factor_rotation.py --rebuild` 建面板）。
检验用 factor_test 的正牌函数（add_forward_returns 带 PIT 空洞防护，D-038）。
输出：factor_results/factor_monitor_ic.parquet、factor_monitor_corr.parquet + 终端摘要。

用法：python scripts/31_factor_monitor.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factor_test import add_forward_returns, rankic_series

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "factor_results"
LIVE_FACTORS = ["turnover_20", "vol_20", "rev_5", "dy_ttm"]  # registry 策略在用
RECENT_DAYS = 60
DECAY_RATIO = 0.30
MIN_FULL_IC = 0.01


def main() -> None:
    if not PANEL_CACHE.exists():
        print(f">> 面板缓存不存在：{PANEL_CACHE}")
        print(">> 先运行 python scripts/30_factor_rotation.py（构建 55 因子面板缓存）")
        sys.exit(2)
    panel = pd.read_parquet(PANEL_CACHE)
    panel["date"] = pd.to_datetime(panel["date"]).astype("datetime64[ns]")
    factors = [c for c in panel.columns
               if c not in ("date", "symbol", "open", "close", "ret")]
    print(f">> 因子监控：{len(factors)} 因子，面板 "
          f"{panel['date'].min().date()}~{panel['date'].max().date()}，{len(panel)} 行")

    panel = add_forward_returns(panel, horizons=(5, 20))

    # ---- 1) 滚动 IC 与衰减 ----
    rows = []
    for f in factors:
        for h in (5, 20):
            ic = rankic_series(panel, f, h)
            # 截面零方差日（如 dy_ttm 早期只有个位数有效值且同 rank）会产生 ±inf
            ic = ic.replace([np.inf, -np.inf], np.nan).dropna()
            if ic.empty:
                continue
            full = float(ic.mean())
            recent = float(ic.iloc[-RECENT_DAYS:].mean())
            dec = (abs(full) >= MIN_FULL_IC and h == 20
                   and (abs(recent) < DECAY_RATIO * abs(full)
                        or np.sign(recent) != np.sign(full)))
            rows.append({"factor": f, "h": h, "ic_full": full,
                         f"ic_recent{RECENT_DAYS}": recent,
                         "nw_note": "", "decay": bool(dec)})
    ic_tab = pd.DataFrame(rows)
    ic_tab["ratio"] = np.where(
        ic_tab["ic_full"].abs() > 0,
        ic_tab[f"ic_recent{RECENT_DAYS}"].abs() / ic_tab["ic_full"].abs(), np.nan)

    print("\n== 在用因子（registry 策略）==")
    live_bad = []
    for f in LIVE_FACTORS:
        r = ic_tab[(ic_tab["factor"] == f) & (ic_tab["h"] == 20)]
        if r.empty:
            print(f"  {f:<12} 面板中无数据（分红面板缺失？）")
            continue
        r = r.iloc[0]
        flag = "⚠️衰减" if r["decay"] else "正常"
        if r["decay"]:
            live_bad.append(f)
        print(f"  {f:<12} 全样本 IC20 {r['ic_full']:+.4f} | "
              f"近{RECENT_DAYS}日 {r[f'ic_recent{RECENT_DAYS}']:+.4f} "
              f"(比值 {r['ratio']:.2f}) {flag}")
    dec_all = ic_tab[ic_tab["decay"]].sort_values("ratio")
    if len(dec_all):
        print(f"\n== 全池衰减因子（{len(dec_all)} 个，含候选）==")
        for _, r in dec_all.head(10).iterrows():
            print(f"  {r['factor']:<12} h{int(r['h'])} 全样本 {r['ic_full']:+.4f} "
                  f"→ 近期 {r[f'ic_recent{RECENT_DAYS}']:+.4f}")

    # ---- 2) 相关性聚类（每 5 日采样，截面 rank 相关矩阵取均值）----
    # 用 pandas 成对删除相关（df.corr）：dy_ttm 等覆盖不全的因子不被整行 dropna 抹掉
    sub = panel[panel["date"].isin(
        panel["date"].drop_duplicates().iloc[::5])].copy()
    mats = []
    for _d, g in sub.groupby("date"):
        m = g[factors].astype("float64")
        if len(m) < 50:
            continue
        c = m.corr().to_numpy()  # 成对删除：每对因子用两者都有效的样本
        mats.append(c)
    stack = np.stack(mats) if mats else None
    if stack is not None:
        with np.errstate(invalid="ignore"):
            avg = np.nanmean(stack, axis=0)
        corr = pd.DataFrame(avg, index=factors, columns=factors)
    else:
        corr = pd.DataFrame()
    pairs = []
    if not corr.empty:
        for i, f1 in enumerate(factors):
            for f2 in factors[i + 1:]:
                v = corr.loc[f1, f2]
                if abs(v) > 0.7:
                    pairs.append({"f1": f1, "f2": f2, "corr": float(v)})
    pairs = sorted(pairs, key=lambda p: abs(p["corr"]), reverse=True)
    hub = (corr.abs().mean(axis=1).sort_values(ascending=False)
           if not corr.empty else pd.Series(dtype=float))
    print(f"\n== 高相关因子对 |corr|>0.7（采样 {len(mats)} 个截面）==")
    for p in pairs[:15]:
        print(f"  {p['f1']:<12} ~ {p['f2']:<12} r={p['corr']:+.2f}")
    if len(hub):
        print("  枢纽因子（平均|corr| 最高）: "
              + ", ".join(f"{k}({v:.2f})" for k, v in hub.head(5).items()))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ic_tab.to_parquet(OUT_DIR / "factor_monitor_ic.parquet", index=False)
    pd.DataFrame(pairs).to_parquet(OUT_DIR / "factor_monitor_corr.parquet",
                                   index=False)
    print(f"\n>> 摘要：{len(factors)} 因子 | 高相关对 {len(pairs)} | "
          f"衰减 {int(ic_tab['decay'].sum())} | 在用因子预警 {len(live_bad)} 个")
    sys.exit(1 if live_bad else 0)


if __name__ == "__main__":
    main()
