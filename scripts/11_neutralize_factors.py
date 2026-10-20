# -*- coding: utf-8 -*-
"""
因子市值中性化检验：低换手/低波/反转 是不是只是"小盘代理"？

做法（逐日横截面）：
  1. 流通市值代理 = 成交额 / (换手率/100)  （由日线 amount、turnover 推导）
  2. 对每个因子做逐日截面回归：factor ~ 1 + log(市值)，取残差 = 市值中性化后的因子
  3. 比较原始因子与中性化因子的 RankIC / NW t 值 / ICIR

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


def _nw_t(ic: pd.Series, lags: int = FWD_DAYS - 1) -> float:
    ic = ic.dropna()
    if len(ic) < 20:
        return np.nan
    return float(_newey_west_tstat(ic, lags))


def main() -> None:
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 构建 PIT 因子面板（{len(symbols)} 只）...", flush=True)
    panel = build_factor_panel(store, symbols, start=START_DATE,
                               pit_constituents=pit)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{len(panel)} 行", flush=True)

    # 市值代理
    size_path = STORE_DIR / "factor_results" / "size_panel.parquet"
    if size_path.exists():
        size = pd.read_parquet(size_path)
    else:
        print(">> 计算流通市值代理（成交额/换手率）...", flush=True)
        frames = []
        for i, sym in enumerate(symbols, 1):
            try:
                df = get_kline(str(sym), adjust="qfq", store=store)
            except FileNotFoundError:
                continue
            d = df[["date", "amount", "turnover"]].copy()
            d["turnover"] = pd.to_numeric(d["turnover"], errors="coerce")
            d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
            d = d[(d["turnover"] > 0) & (d["amount"] > 0)]
            d["size"] = d["amount"] / (d["turnover"] / 100.0)
            d["symbol"] = str(sym)
            frames.append(d[["date", "symbol", "size"]])
            if i % 200 == 0:
                print(f"   {i}/{len(symbols)}", flush=True)
        size = pd.concat(frames, ignore_index=True)
        size_path.parent.mkdir(parents=True, exist_ok=True)
        size.to_parquet(size_path, index=False)
    print(f"   市值代理 {len(size)} 行", flush=True)

    # 20 日前瞻收益
    px = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    fwd = (px.shift(-FWD_DAYS) / px - 1.0)
    fwd_long = fwd.stack().rename("fwd").reset_index()
    fwd_long.columns = ["date", "symbol", "fwd"]

    cache = STORE_DIR / "factor_results" / "neutralization_input.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        print(f"   读取缓存 {len(df)} 行", flush=True)
    else:
        df = panel.merge(size, on=["date", "symbol"], how="left")
        df = df.merge(fwd_long, on=["date", "symbol"], how="left")
        df["log_size"] = np.log(df["size"].clip(lower=1.0))
        df.to_parquet(cache, index=False)
        print(f"   合并后 {len(df)} 行，市值缺失 {df['size'].isna().mean():.1%}", flush=True)

    # 纯 numpy 按日分组（避开 pandas groupby.apply 在 Windows 上的原生崩溃）
    base = df.dropna(subset=["fwd", "size"]).sort_values("date").reset_index(drop=True)
    dates = base["date"].to_numpy()
    uniq, starts = np.unique(dates, return_index=True)
    bounds = list(starts) + [len(base)]

    def _by_date(vals: np.ndarray, extra_mask: np.ndarray | None = None):
        out = []
        for k in range(len(uniq)):
            s, e = bounds[k], bounds[k + 1]
            v = vals[s:e]
            m = np.isfinite(v)
            if extra_mask is not None:
                m &= extra_mask[s:e]
            out.append((s, e, m))
        return out

    log_size = base["log_size"].to_numpy(float)
    fwd = base["fwd"].to_numpy(float)
    masks = _by_date(log_size)
    ic_size = []
    for k, (s, e, m) in enumerate(masks):
        if m.sum() < 30:
            continue
        ic_size.append(_pearson(_rank(log_size[s:e][m]), _rank(fwd[s:e][m])))
    ic_size = pd.Series(ic_size).dropna()

    rows = [{"factor": "(市值 log_size)", "raw_ic": float(ic_size.mean()),
             "raw_icir": float(ic_size.mean() / ic_size.std()) if ic_size.std() > 0 else np.nan,
             "raw_nw_t": _nw_t(ic_size), "neu_ic": np.nan,
             "neu_icir": np.nan, "neu_nw_t": np.nan}]

    for fac in FACTORS:
        y_all = base[fac].to_numpy(float)
        ic_raw, ic_neu = [], []
        for k, (s, e, _) in enumerate(masks):
            sl = slice(s, e)
            m = np.isfinite(y_all[sl]) & np.isfinite(log_size[sl]) & np.isfinite(fwd[sl])
            if m.sum() < 30:
                continue
            y = y_all[sl][m]
            ls = log_size[sl][m]
            f = fwd[sl][m]
            ic_raw.append(_pearson(_rank(y), _rank(f)))
            # 闭式解单变量 OLS（避开 np.linalg.lstsq 在本环境的原生崩溃）
            xs = ls - ls.mean()
            ys = y - y.mean()
            den = float((xs * xs).sum())
            beta = float((xs * ys).sum() / den) if den > 0 else 0.0
            resid = ys - beta * xs
            ic_neu.append(_pearson(_rank(resid), _rank(f)))
        s_raw = pd.Series(ic_raw).dropna()
        s_neu = pd.Series(ic_neu).dropna()
        rows.append({
            "factor": fac,
            "raw_ic": float(s_raw.mean()), "raw_icir": float(s_raw.mean() / s_raw.std()) if s_raw.std() > 0 else np.nan,
            "raw_nw_t": _nw_t(s_raw), "neu_ic": float(s_neu.mean()),
            "neu_icir": float(s_neu.mean() / s_neu.std()) if s_neu.std() > 0 else np.nan,
            "neu_nw_t": _nw_t(s_neu)})
        print(f"   {fac}: raw IC {s_raw.mean():+.4f} (t={_nw_t(s_raw):+.2f}) | "
              f"中性化 {s_neu.mean():+.4f} (t={_nw_t(s_neu):+.2f})", flush=True)

    out = pd.DataFrame(rows)
    out = out.reindex(out["raw_nw_t"].abs().sort_values(ascending=False).index)
    out_path = STORE_DIR / "factor_results" / "neutralization_summary.parquet"
    out.to_parquet(out_path, index=False)

    print()
    print("== 市值中性化前后对比（20 日前瞻收益，RankIC / NW-t）==")
    show = out.copy()
    for c in ["raw_ic", "neu_ic"]:
        show[c] = show[c].map(lambda v: f"{v:+.4f}" if pd.notna(v) else "-")
    for c in ["raw_nw_t", "neu_nw_t"]:
        show[c] = show[c].map(lambda v: f"{v:+.2f}" if pd.notna(v) else "-")
    show["raw_icir"] = show["raw_icir"].map(lambda v: f"{v:+.3f}" if pd.notna(v) else "-")
    show["neu_icir"] = show["neu_icir"].map(lambda v: f"{v:+.3f}" if pd.notna(v) else "-")
    print(show.to_string(index=False))
    print(f"\n已保存: {out_path}")


if __name__ == "__main__":
    main()
