# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\MeineArbeit\QuantDex")
import pandas as pd, numpy as np
from config import STORE_DIR

def stats(nav, label):
    nav = nav.dropna()
    r = nav.pct_change().dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    total = nav.iloc[-1] / nav.iloc[0] - 1
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(252)
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    dd = (nav / nav.cummax() - 1).min()
    print(f"{label}: 区间 {nav.index[0].date()}~{nav.index[-1].date()} | 总收益 {total:+.2%} | 年化 {ann:+.2%} | 波动 {vol:.2%} | 夏普 {sharpe:.2f} | 最大回撤 {dd:.2%}")

print("== 1) 组合回测 (05_portfolio_backtest) ==")
df = pd.read_parquet(STORE_DIR / "portfolio_results" / "nav_000300_top50_reb20.parquet")
print("列:", list(df.columns))
df = df.set_index("date") if "date" in df.columns else df
for col in df.columns:
    stats(df[col].rename(col), f"  {col}")

print()
print("== 2) walk-forward 样本外 (06_walkforward) ==")
oos = pd.read_parquet(STORE_DIR / "walkforward_results" / "nav_oos_000300.parquet")
print("列:", list(oos.columns))
oos = oos.set_index("date") if "date" in oos.columns else oos
for col in oos.columns:
    stats(oos[col].rename(col), f"  {col}")

print()
print("== 3) fold 明细 ==")
folds = pd.read_parquet(STORE_DIR / "walkforward_results" / "folds_000300.parquet")
print(folds.to_string(index=False))

print()
print("== 4) 因子分层回测(5层净值存在 factor_results) ==")
ic = pd.read_parquet(STORE_DIR / "factor_results" / "ic_summary.parquet")
cols = [c for c in ["factor","nw_tstat","rank_ic","icir"] if c in ic.columns]
print(ic[cols].head(8).to_string(index=False))
