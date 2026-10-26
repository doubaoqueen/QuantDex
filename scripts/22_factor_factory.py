# -*- coding: utf-8 -*-
"""
表达式因子工厂检验（D-031）：AlphaLite32 逐因子真数据 IC 筛选。

流程：PIT 面板（含表达式因子）→ 逐因子 RankIC/ICIR/NW-t（5/20 日前瞻）
      → 覆盖率 → 结果落 factor_results/expr_ic_summary.parquet → 实验存档。

用法：
    python scripts/22_factor_factory.py               # 全部 AlphaLite32
    python scripts/22_factor_factory.py KMID ROC20    # 只验指定因子
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis import expr_factors as EF
from src.analysis.experiments import record
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import build_factor_panel
from src.data import universe
from src.data.storage import ParquetStore

START_DATE = "20180101"


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    x = x - x.mean()
    y = y - y.mean()
    den = np.sqrt((x * x).sum() * (y * y).sum())
    return float((x * y).sum() / den) if den > 0 else np.nan


def _rank(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1, dtype=float)
    return ranks


def main() -> None:
    names = sys.argv[1:] or EF.EXPR_NAMES
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    exprs = {k: EF.EXPR_FACTORS[k]["expr"] for k in names if k in EF.EXPR_FACTORS}
    print(f">> 构建 PIT 面板（{len(symbols)} 只历史成分）+ 表达式因子 {len(exprs)} 个...",
          flush=True)
    panel = build_factor_panel(store, symbols, start=START_DATE,
                               pit_constituents=pit, expr_factors=exprs)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{len(panel)} 行", flush=True)

    px = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    # D-038 可成交口径：T 收盘出信号、T+1 建仓、持有 h 日（剔隔夜跳空）
    fwd = {h: (px.shift(-(h + 1)) / px.shift(-1) - 1.0) for h in (5, 20)}
    fwd_long = {h: f.stack().rename("fwd").reset_index()
                .rename(columns={"level_0": "date", "level_1": "symbol"})
                for h, f in fwd.items()}

    rows = []
    for f in names:
        col = panel[["date", "symbol", f]].copy()
        # 覆盖率：该因子在有效截面中的非缺失比例（时序均值）
        cov = col.dropna(subset=[f]).groupby("date").size()
        cov_ratio = float((cov > 30).mean())
        row = {"factor": f, "family": EF.EXPR_FACTORS[f]["family"],
               "coverage": cov_ratio}
        for h in (5, 20):
            m = col.merge(fwd_long[h], on=["date", "symbol"], how="inner").dropna()
            ics = []
            for d, g in m.groupby("date"):
                if len(g) < 30:
                    continue
                ics.append(_pearson(_rank(g[f].to_numpy(float)),
                                    _rank(g["fwd"].to_numpy(float))))
            s = pd.Series(ics).dropna()
            row[f"ic{h}"] = float(s.mean())
            row[f"t{h}"] = float(_newey_west_tstat(s, h - 1)) if len(s) >= 20 else np.nan
            row[f"icir{h}"] = float(s.mean() / s.std()) if s.std() > 0 else np.nan
            row[f"days{h}"] = int(len(s))
        rows.append(row)
        print(f"   {f:<11} IC20 {row['ic20']:+.4f} (t {row['t20']:+.2f})  "
              f"IC5 {row['ic5']:+.4f} (t {row['t5']:+.2f})  覆盖 {cov_ratio:.0%}",
              flush=True)

    out = pd.DataFrame(rows)
    # R3-12 多重检验校正：BH-FDR q 值（p 由 t 的正态近似，46 因子期望 2-4 个假显著）
    from math import erfc, sqrt
    out["p20"] = [erfc(abs(v) / sqrt(2)) if pd.notna(v) else np.nan
                  for v in out["t20"]]
    m = out["p20"].notna().sum()
    pvals = out.loc[out["p20"].notna(), "p20"].sort_values()
    qvals = (pvals * m / (pvals.rank(method="first"))).clip(upper=1.0)
    out["q20"] = qvals.reindex(out.index)
    out_dir = STORE_DIR / "factor_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 合并历史结果（同因子新结果覆盖旧结果），避免分批筛选互相覆盖
    summary_path = out_dir / "expr_ic_summary.parquet"
    if summary_path.exists():
        old = pd.read_parquet(summary_path)
        out = pd.concat([old[~old["factor"].isin(out["factor"])], out],
                        ignore_index=True)
    out = out.reindex(out["t20"].abs().sort_values(ascending=False).index)
    out.to_parquet(summary_path, index=False)

    n_sig = int((out["t20"].abs() > 2).sum())
    print()
    print("== AlphaLite32 表达式因子筛选（|t20|>2 显著）==")
    show = out.copy()
    for c in ["ic5", "ic20", "icir5", "icir20"]:
        show[c] = show[c].map(lambda v: f"{v:+.4f}")
    for c in ["t5", "t20"]:
        show[c] = show[c].map(lambda v: f"{v:+.2f}")
    show["coverage"] = show["coverage"].map(lambda v: f"{v:.0%}")
    print(show.to_string(index=False))
    print(f"\n显著因子 {n_sig}/{len(out)}；已保存 {out_dir / 'expr_ic_summary.parquet'}")

    try:
        record("expr_factor_screen",
               params={"panel_start": START_DATE, "index": "000300",
                       "factors": names},
               metrics={r["factor"]: float(r["t20"]) for r in out.to_dict("records")},
               notes=f"AlphaLite32 表达式因子 IC 筛选，20日前瞻 NW-t；显著 {n_sig}/{len(out)}",
               artifacts=["factor_results/expr_ic_summary.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
