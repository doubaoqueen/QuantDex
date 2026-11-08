"""第 39 步：因子入池闸门 v1（路线 2 主战场，D-073）。

问题：55 个因子里 12 个 |t|>2 显著却 0 个进策略——"检验→入池"环节不存在
（2026-11-07 审计 + D-068）。本脚本实现闸门 v1 的前三关，产出**可准入候选清单**：

  关 1 去重：|corr|>0.7 的因子簇只留 |IC20| 最强者（数据：31 号监控的相关矩阵）；
  关 2 强度：|IC20| ≥ 0.03 且全样本 NW-t ≥ 2；
  关 3 稳定性：分年 IC 方向一致 ≥ 6/7 年（2020-2026）——S0/D-064 的统计纪律
     上移到因子层；且近 60 日未衰减（31 号 decay 标记为 False）。

已知在用 4 因子（turnover_20/vol_20/rev_5/dy_ttm）作为"现任"直接进入对照，
闸门对它们同样打分——若现任也被某关拦下，说明阈值过严需要复核。
结论三种：可准入（新面孔）/ 现任守住 / 全部拦下（当前组合已是池中最优——
这本身是对"因子浪费"问题的最终回答）。

口径：面板为 rotation_panel_all55（含 ST——研究口径，历史结果可复现；
D-071 的实盘剔除不影响本闸门的相对比较）。分年 IC 用 factor_test 的正牌函数
（D-038 可成交口径 + PIT 空洞防护）。
用法：python scripts/39_factor_gate.py
"""
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factor_test import (
    _newey_west_tstat, add_forward_returns, rankic_series,
)
from src.analysis.experiments import record

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
MONITOR_IC = STORE_DIR / "factor_results" / "factor_monitor_ic.parquet"
MONITOR_CORR = STORE_DIR / "factor_results" / "factor_monitor_corr.parquet"
OUT_DIR = STORE_DIR / "research_results"
LIVE_FACTORS = ["turnover_20", "vol_20", "rev_5", "dy_ttm"]
MIN_IC = 0.03
MIN_T = 2.0
MIN_YEARS_CONSISTENT = 6


def dedup(pairs: pd.DataFrame, ic20: dict) -> list[str]:
    """关 1：|corr|>0.7 簇内只留 |IC20| 最强者（贪心，按强度降序吸收）。

    pairs = 31 号监控的高相关对清单（长表 f1/f2/corr，已过滤 >0.7）。
    """
    adj: dict[str, set] = {}
    for r in pairs.itertuples(index=False):
        adj.setdefault(r.f1, set()).add(r.f2)
        adj.setdefault(r.f2, set()).add(r.f1)
    order = sorted(ic20, key=lambda f: abs(ic20.get(f, 0)), reverse=True)
    kept, dropped = [], {}
    for f in order:
        clash = next((k for k in kept if k in adj.get(f, ())), None)
        if clash is not None:
            dropped[f] = clash
            continue
        kept.append(f)
    print(f"  [关1] 去重：{len(order)} → {len(kept)}（剔除 {len(dropped)} 个高相关）")
    for f, keeper in sorted(dropped.items(), key=lambda kv: kv[1]):
        print(f"    {f:<12} 被 {keeper} 吸收")
    return kept


def yearly_consistency(ic: pd.Series, years=(2020, 2021, 2022, 2023, 2024, 2025, 2026)):
    """关 3 素材：分年 IC 均值 → {年: 符号}。"""
    by_year = ic.groupby(ic.index.year).mean()
    return {y: float(by_year.get(y, np.nan)) for y in years}


def main() -> None:
    for path, hint in [(PANEL_CACHE, "30 号面板缓存"), (MONITOR_IC, "31 号监控"),
                       (MONITOR_CORR, "31 号相关矩阵")]:
        if not Path(path).exists():
            print(f">> 缺 {path}——先跑 30/31（{hint}）")
            sys.exit(2)

    mon = pd.read_parquet(MONITOR_IC)
    corr_pairs = pd.read_parquet(MONITOR_CORR)
    ic20_all = {r["factor"]: float(r["ic_full"]) for _, r in
                mon[mon["h"] == 20].iterrows()}
    decayed = set(mon[mon["decay"] & (mon["h"] == 20)]["factor"])

    panel = pd.read_parquet(PANEL_CACHE)
    panel["date"] = pd.to_datetime(panel["date"]).astype("datetime64[ns]")
    panel["symbol"] = panel["symbol"].astype(str)
    factors = [c for c in panel.columns
               if c not in ("date", "symbol", "open", "close", "ret")]

    # ---- 关 1 ----
    print(f">> 因子闸门 v1：{len(factors)} 因子进闸", flush=True)
    survivors = dedup(corr_pairs, ic20_all)

    # ---- 关 2 + 关 3（逐因子算分年 IC 与 NW-t）----
    panel = add_forward_returns(panel, horizons=(20,))
    rows = []
    for f in survivors:
        ic = rankic_series(panel, f, 20)
        ic = ic.replace([np.inf, -np.inf], np.nan).dropna()
        if len(ic) < 500:
            continue
        yc = yearly_consistency(ic)
        signs = [np.sign(v) for v in yc.values() if pd.notna(v)]
        base_sign = np.sign(ic.mean())
        consistent = sum(1 for s in signs if s == base_sign and s != 0)
        tstat = float(_newey_west_tstat(ic, 19))
        rows.append({"factor": f, "ic20": float(ic.mean()), "nw_t": tstat,
                     "years_consistent": consistent,
                     "yearly": "|".join(f"{y}:{yc[y]:+.3f}" for y in yc),
                     "decayed": f in decayed,
                     "is_live": f in LIVE_FACTORS})
    gate = pd.DataFrame(rows)
    gate["pass_strength"] = (gate["ic20"].abs() >= MIN_IC) & (gate["nw_t"].abs() >= MIN_T)
    gate["pass_stable"] = (gate["years_consistent"] >= MIN_YEARS_CONSISTENT) & (~gate["decayed"])
    gate["admit"] = gate["pass_strength"] & gate["pass_stable"]

    gate = gate.sort_values("nw_t", key=lambda s: s.abs(), ascending=False)
    print("\n== 闸门评分（按 |NW-t| 降序）==")
    print(f"{'因子':<12} {'IC20':>8} {'NW-t':>7} {'一致年':>5} {'衰减':>4} "
          f"{'强度':>4} {'稳定':>4} {'准入':>4} {'现任':>4}")
    for _, r in gate.iterrows():
        print(f"{r['factor']:<12} {r['ic20']:>+8.4f} {r['nw_t']:>+7.2f} "
              f"{r['years_consistent']:>4}/7 {'是' if r['decayed'] else '否':>4} "
              f"{'✓' if r['pass_strength'] else '✗':>4} "
              f"{'✓' if r['pass_stable'] else '✗':>4} "
              f"{'✓' if r['admit'] else '✗':>4} {'现任' if r['is_live'] else '':>4}")

    new_admits = gate[gate["admit"] & ~gate["is_live"]]
    live = gate[gate["is_live"]]
    print("\n== 结论 ==")
    print(f"  新准入候选：{len(new_admits)} 个"
          + (f"：{', '.join(new_admits['factor'])}" if len(new_admits) else ""))
    print(f"  现任通过：{int(live['admit'].sum())}/{len(live)}"
          + ("" if live.empty else
             f"（未过：{', '.join(live[~live['admit']]['factor'])}）" if (live['admit'] == False).any() else ""))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gate.to_parquet(OUT_DIR / "factor_gate_v1.parquet", index=False)
    try:
        record("factor_gate_v1",
               params={"pool": len(factors), "after_dedup": len(survivors),
                       "min_ic": MIN_IC, "min_t": MIN_T,
                       "min_years": MIN_YEARS_CONSISTENT},
               metrics={"admitted_new": int(len(new_admits)),
                        "live_passed": int(live["admit"].sum())},
               notes="入池闸门 v1：去重+强度+分年稳定三关；结论见 DECISIONS D-073",
               artifacts=["research_results/factor_gate_v1.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
