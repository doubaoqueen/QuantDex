# -*- coding: utf-8 -*-
"""
因子中性化四口径检验：低换手/低波/反转 是"小盘代理"还是"行业代理"？

口径（逐日横截面，同一有效样本上比较，避免样本差异污染对比）：
  raw   原始因子
  size  市值中性化：factor ~ 1 + log(市值代理)，取残差
  ind   行业中性化：factor - 行业均值（等价于对申万一级行业哑变量回归取残差）
  both  市值+行业中性化：组内去均值后做 FWL 回归（等价于 [1, log市值, 行业哑变量]）

行业数据：meta/industry_sw（申万一级 31 行业，2026-10-24 抓取）。
两个已知限制（结论解读时注意）：
  ① 用**当前**行业分类回溯 2018-2026 全历史——分类本身有前视（免费源无行业 PIT）；
  ② 退市股约 71 只无行业映射，被剔除（占 PIT 并集约 7%）。

输出：data/store/factor_results/neutralization_summary.parquet + 终端表
用法：python scripts/11_neutralize_factors.py
"""
import sys

import numpy as np
import pandas as pd

import _bootstrap  # noqa: F401

from config import STORE_DIR
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import build_factor_panel
from src.data import universe
from src.data.market import get_kline
from src.data.storage import ParquetStore

START_DATE = "20180101"
FACTORS = ["turnover_20", "vol_20", "rev_5", "mom_20", "mom_60",
           "rsi_14", "ma60_bias", "amihud_20"]
FWD_DAYS = 20


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


def _nw_t(ic: list, lags: int = FWD_DAYS - 1) -> float:
    s = pd.Series(ic).dropna()
    if len(s) < 20:
        return np.nan
    return float(_newey_west_tstat(s, lags))


def _group_demean(x: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """组内去均值（纯 numpy，等价于对行业哑变量回归取残差）。"""
    ngroup = int(codes.max()) + 1
    sums = np.bincount(codes, weights=x, minlength=ngroup)
    cnts = np.bincount(codes, minlength=ngroup)
    means = sums / np.maximum(cnts, 1)
    return x - means[codes]


def _ols_resid(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """闭式单变量 OLS 残差（避开 np.linalg.lstsq 在本环境的原生崩溃）。"""
    xs = x - x.mean()
    ys = y - y.mean()
    den = float((xs * xs).sum())
    beta = float((xs * ys).sum() / den) if den > 0 else 0.0
    return ys - beta * xs


def main() -> None:
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)

    cache = STORE_DIR / "factor_results" / "neutralization_input.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        print(f">> 读取缓存面板输入 {len(df)} 行", flush=True)
    else:
        print(f">> 构建 PIT 因子面板（{len(symbols)} 只）...", flush=True)
        panel = build_factor_panel(store, symbols, start=START_DATE,
                                   pit_constituents=pit)
        size_path = STORE_DIR / "factor_results" / "size_panel.parquet"
        if size_path.exists():
            size = pd.read_parquet(size_path)
        else:
            print(">> 计算流通市值代理（成交额/换手率）...", flush=True)
            frames = []
            for i, sym in enumerate(symbols, 1):
                try:
                    k = get_kline(str(sym), adjust="qfq", store=store)
                except FileNotFoundError:
                    continue
                d = k[["date", "amount", "turnover"]].copy()
                d["turnover"] = pd.to_numeric(d["turnover"], errors="coerce")
                d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
                d = d[(d["turnover"] > 0) & (d["amount"] > 0)]
                d["size"] = d["amount"] / (d["turnover"] / 100.0)
                d["symbol"] = str(sym)
                frames.append(d[["date", "symbol", "size"]])
                if i % 200 == 0:
                    print(f"   {i}/{len(symbols)}", flush=True)
            size = pd.concat(frames, ignore_index=True)
            size.to_parquet(size_path, index=False)
        px = panel.pivot(index="date", columns="symbol", values="close").sort_index()
        fwd = (px.shift(-FWD_DAYS) / px - 1.0)
        fwd_long = fwd.stack().rename("fwd").reset_index()
        fwd_long.columns = ["date", "symbol", "fwd"]
        df = panel.merge(size, on=["date", "symbol"], how="left")
        df = df.merge(fwd_long, on=["date", "symbol"], how="left")
        df.to_parquet(cache, index=False)
    df["log_size"] = np.log(df["size"].clip(lower=1.0))

    # 行业映射（申万一级，当前分类）
    ind = store.read("meta", "industry_sw")[["symbol", "industry"]].copy()
    ind["symbol"] = ind["symbol"].astype(str)
    df["symbol"] = df["symbol"].astype(str)
    df = df.merge(ind, on="symbol", how="left")
    n_no_ind = int(df["industry"].isna().sum())
    print(f">> 行业映射：{df['industry'].notna().sum()} 行有行业，"
          f"{n_no_ind} 行无（退市股，剔除）", flush=True)

    # 四口径统一在 size+industry 均有效的样本上计算
    base = df.dropna(subset=["fwd", "size", "industry"]).sort_values("date")
    base = base.reset_index(drop=True)
    codes_all, _ = pd.factorize(base["industry"])
    codes_all = codes_all.astype(int)

    dates = base["date"].to_numpy()
    uniq, starts = np.unique(dates, return_index=True)
    bounds = list(starts) + [len(base)]
    log_size = base["log_size"].to_numpy(float)
    fwd = base["fwd"].to_numpy(float)
    print(f">> 有效样本 {len(base)} 行 / {len(uniq)} 个交易日", flush=True)

    def day_iter():
        for k in range(len(uniq)):
            yield slice(bounds[k], bounds[k + 1])

    rows = []
    for fac in FACTORS:
        y_all = base[fac].to_numpy(float)
        ic = {"raw": [], "size": [], "ind": [], "both": []}
        for sl in day_iter():
            m = (np.isfinite(y_all[sl]) & np.isfinite(log_size[sl])
                 & np.isfinite(fwd[sl]))
            if m.sum() < 30:
                continue
            y = y_all[sl][m]
            s = log_size[sl][m]
            f = fwd[sl][m]
            g = codes_all[sl][m]
            rk_f = _rank(f)
            ic["raw"].append(_pearson(_rank(y), rk_f))
            ic["size"].append(_pearson(_rank(_ols_resid(y, s)), rk_f))
            y_dm = _group_demean(y, g)
            ic["ind"].append(_pearson(_rank(y_dm), rk_f))
            s_dm = _group_demean(s, g)
            ic["both"].append(_pearson(_rank(_ols_resid(y_dm, s_dm)), rk_f))
        row = {"factor": fac}
        for key, arr in ic.items():
            ser = pd.Series(arr).dropna()
            row[f"{key}_ic"] = float(ser.mean())
            row[f"{key}_icir"] = float(ser.mean() / ser.std()) if ser.std() > 0 else np.nan
            row[f"{key}_nw_t"] = _nw_t(arr)
        rows.append(row)
        print(f"   {fac}: raw {row['raw_ic']:+.4f}(t={row['raw_nw_t']:+.2f}) | "
              f"size {row['size_ic']:+.4f}({row['size_nw_t']:+.2f}) | "
              f"ind {row['ind_ic']:+.4f}({row['ind_nw_t']:+.2f}) | "
              f"both {row['both_ic']:+.4f}({row['both_nw_t']:+.2f})", flush=True)

    out = pd.DataFrame(rows)
    out = out.reindex(out["raw_nw_t"].abs().sort_values(ascending=False).index)
    out_path = STORE_DIR / "factor_results" / "neutralization_summary.parquet"
    out.to_parquet(out_path, index=False)

    print()
    print("== 中性化四口径对比（20 日前瞻 RankIC / NW-t，同一有效样本）==")
    show = out.copy()
    for c in show.columns:
        if c.endswith("_ic"):
            show[c] = show[c].map(lambda v: f"{v:+.4f}")
        elif c.endswith("_nw_t"):
            show[c] = show[c].map(lambda v: f"{v:+.2f}")
        elif c.endswith("_icir"):
            show = show.drop(columns=[c])
    print(show.to_string(index=False))
    print(f"\n已保存: {out_path}")


if __name__ == "__main__":
    main()
