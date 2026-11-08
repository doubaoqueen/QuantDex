"""第 42 步：涨停板研究——事件/情绪/行业三假设检验（用户 2026-11-09 构想，D-078）。

数据：949 只 PIT 并集日线（2018-2026，离线，不碰 bs）。
判定（与撮合/涨跌停卡片同源）：pct = close/prev_close-1，pct ≥ 涨跌幅限制-1e-3
为收盘涨停；high/prev_close-1 ≥ 限制-1e-3 且收盘未封 = 盘中触板回落；
ST 股 ±5% 用证券简称判定（研究口径：全池含 ST，与市场真实观感一致）。

三假设：
  H1 个股动量：收盘涨停事件后 1/5/20 日，超额 vs 当日池中位（分封板/一字板）；
  H2 市场情绪：每日 涨停/(涨停+跌停) 比率 → 沪深300 未来 5/20 日收益
     （比率五分位对比 + 分年一致性）；
  H3 行业方向：行业近 5 日涨停聚集度 → 该行业等权组合未来 5 日收益
     （逐行业 RankIC + 五分位对比）——用户说的"归纳出方向"。
纪律：分年方向一致 + NW-t 才可采纳（D-064 同款）。
用法：python scripts/42_limitup_research.py
"""
import faulthandler
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR, board_limit
from src.analysis.factor_test import _newey_west_tstat
from src.data.storage import ParquetStore

OUT_DIR = STORE_DIR / "research_results"


def load_pool_bars(store: ParquetStore) -> pd.DataFrame:
    """全部日线 → 长表（symbol, date, open, high, low, close, prev_close, pct, lim）。"""
    names, _ = {}, {}
    try:
        sec = store.read("meta", "bs_securities", columns=["symbol", "name"])
        names = dict(zip(sec["symbol"].astype(str), sec["name"].astype(str)))
    except Exception:  # noqa: BLE001
        pass
    frames = []
    for fp in sorted(glob_files(store)):
        sym = fp.stem.split("__")[0]
        try:
            df = store.read("daily", fp.stem, columns=["date", "open", "high", "low", "close"])
        except Exception:  # noqa: BLE001
            continue
        if df.empty:
            continue
        df = df.sort_values("date")
        prev = df["close"].shift(1)
        pct = df["close"] / prev - 1.0
        lim = board_limit(sym, names.get(sym))
        frames.append(pd.DataFrame({
            "symbol": sym, "date": pd.to_datetime(df["date"]),
            "open": df["open"].to_numpy(), "high": df["high"].to_numpy(),
            "low": df["low"].to_numpy(), "close": df["close"].to_numpy(),
            "prev_close": prev.to_numpy(), "pct": pct.to_numpy(),
            "lim": lim,
        }))
    bars = pd.concat(frames, ignore_index=True)
    bars = bars.dropna(subset=["pct"])
    return bars


def glob_files(store: ParquetStore):
    return sorted((STORE_DIR / "market" / "daily").glob("*__qfq.parquet"))


def main() -> None:
    faulthandler.enable(file=sys.stderr)  # 段错误也打印 Python 堆栈（本机 11-08/11-09 原生崩溃诊断）
    store = ParquetStore()
    print(">> 读取全池日线并判定涨跌停...", flush=True)
    bars = load_pool_bars(store)
    bars["date"] = pd.to_datetime(bars["date"])
    # 一字板：开=收=高=低 全在涨停价上（近似：pct≥lim 且 open≈close 且 low≥涨停价-容差）
    at_limit = bars["pct"] >= bars["lim"] - 1e-3
    one_word = at_limit & ((bars["open"] / bars["prev_close"] - 1) >= bars["lim"] - 1e-3) \
        & ((bars["low"] / bars["prev_close"] - 1) >= bars["lim"] - 2e-3)
    bars["ev_up_close"] = at_limit
    bars["ev_up_one_word"] = one_word
    bars["ev_up_touch"] = (bars["high"] / bars["prev_close"] - 1 >= bars["lim"] - 1e-3)
    bars["ev_down_close"] = bars["pct"] <= -(bars["lim"] - 1e-3)
    bars["year"] = bars["date"].dt.year

    up_days = bars.groupby("date")["ev_up_close"].sum()
    down_days = bars.groupby("date")["ev_down_close"].sum()
    print(f"   全池 {bars['symbol'].nunique()} 只，{bars['date'].nunique()} 个交易日；"
          f"收盘涨停事件 {int(at_limit.sum())} 起（一字板 {int(one_word.sum())}），"
          f"跌停事件 {int(bars['ev_down_close'].sum())} 起", flush=True)

    # 前向收益矩阵（close→close，n 日）
    px = bars.pivot(index="date", columns="symbol", values="close").sort_index()
    fwd = {n: px.shift(-n) / px - 1.0 for n in (1, 5, 20)}
    pool_med = {n: fwd[n].median(axis=1) for n in fwd}

    # ---- H1 个股涨停事件研究 ----
    ev = bars[at_limit].copy()
    rows = []
    for r in ev.itertuples(index=False):
        t = r.date
        if t not in px.index:
            continue
        row = {"date": t, "year": t.year, "symbol": r.symbol,
               "one_word": bool(r.ev_up_one_word)}
        for n in (1, 5, 20):
            f = fwd[n].loc[t, r.symbol] if r.symbol in px.columns else np.nan
            m = pool_med[n].loc[t] if t in pool_med[n].index else np.nan
            if pd.notna(f) and pd.notna(m):
                row[f"fwd{n}"] = float(f)
                row[f"exc{n}"] = float(f - m)
        rows.append(row)
    evdf = pd.DataFrame(rows)
    print("\n== H1 个股：涨停（收盘封板）事件后超额收益（vs 当日池中位）==")
    for n in (1, 5, 20):
        sub = evdf.dropna(subset=[f"exc{n}"])
        if sub.empty:
            continue
        ic_series = sub.groupby("date")[f"exc{n}"].mean().dropna()
        nw = _newey_west_tstat(ic_series, 19) if len(ic_series) > 30 else np.nan
        print(f"  fwd{n:<3} 均值 {sub[f'exc{n}'].mean():+.3%} | 中位 "
              f"{sub[f'exc{n}'].median():+.3%} | 跑赢池比例 "
              f"{(sub[f'exc{n}'] > 0).mean():.0%} | 事件日聚类 NW-t {nw:+.2f}")

    # ---- H2 市场情绪择时 ----
    senti = pd.DataFrame({"up": up_days, "down": down_days}).fillna(0)
    senti["ratio"] = (senti["up"] + 1) / (senti["up"] + senti["down"] + 2)  # 平滑
    b = store.read("daily", "000300__none", columns=["date", "close"])
    b300 = pd.Series(b["close"].to_numpy(float), index=pd.to_datetime(b["date"])).sort_index()
    mkt_fwd5 = b300.shift(-5) / b300 - 1.0
    mkt_fwd20 = b300.shift(-20) / b300 - 1.0
    senti["mkt_fwd5"] = mkt_fwd5.reindex(senti.index)
    senti["mkt_fwd20"] = mkt_fwd20.reindex(senti.index)
    senti["year"] = senti.index.year
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    senti.to_parquet(OUT_DIR / "limitup_senti.parquet")  # 中间结果先落盘（防原生崩溃丢数据）
    s = senti.dropna(subset=["ratio", "mkt_fwd5", "mkt_fwd20"])
    q = pd.qcut(s["ratio"], 5, labels=False, duplicates="drop")
    print("\n== H2 情绪：涨停占比五分位 → 沪深300 未来收益 ==", flush=True)
    for n, col in (("5日", "mkt_fwd5"), ("20日", "mkt_fwd20")):
        g = s.groupby(q)[col].mean()
        ic = {}
        for y, x in s.groupby(s.index.year):
            if len(x) <= 100:
                continue
            a = x["ratio"].to_numpy(float)
            b = x[col].to_numpy(float)
            a, b = a - a.mean(), b - b.mean()
            den = np.sqrt((a * a).sum() * (b * b).sum())
            ic[y] = float((a * b).sum() / den) if den > 0 else np.nan
        ic = pd.Series(ic).dropna()
        consistent = bool((ic > 0).all() or (ic < 0).all())
        print(f"  未来{col[-2:]}: Q1(冰点) {g.iloc[0]:+.2%} → Q5(亢奋) {g.iloc[-1]:+.2%} "
              f"| 分年相关方向{'一致' if consistent else '混乱'}（{len(ic)} 年）")
        for yy, v in ic.items():
            print(f"    {yy}: r={v:+.3f}")

    # ---- H3 行业方向 ----
    try:
        ind_df = store.read("meta", "industry_sw", columns=["symbol", "industry"])
        ind_map = dict(zip(ind_df["symbol"].astype(str), ind_df["industry"]))
    except Exception:  # noqa: BLE001
        ind_map = {}
    bars["industry"] = bars["symbol"].map(ind_map)
    up_ind = bars[bars["ev_up_close"]].groupby(["date", "industry"]).size()
    up_ind = up_ind.unstack(fill_value=0)
    ind_list = sorted(set(ind_map.get(str(c)) for c in px.columns) - {None})
    ind_ret = pd.DataFrame(index=px.index, columns=ind_list, dtype=float)
    sym_ind = {str(c): ind_map.get(str(c)) for c in px.columns}
    for ind in ind_list:
        members = [c for c in px.columns if sym_ind.get(str(c)) == ind]
        if members:
            ind_ret[ind] = px[members].mean(axis=1)
    ind_fwd5 = ind_ret.shift(-5) / ind_ret - 1.0
    up5 = up_ind.rolling(5).sum().reindex(ind_ret.index).fillna(0)
    rows3 = []
    for ind in ind_list:
        if ind not in up5.columns or ind not in ind_fwd5.columns:
            continue
        x = up5[ind]
        y = ind_fwd5[ind]
        m = pd.concat([x, y], axis=1, keys=["x", "y"]).dropna()
        if len(m) < 300:
            continue
        rx = m["x"].rank()
        ry = m["y"].rank()
        d = (rx - rx.mean()) * (ry - ry.mean())
        den = np.sqrt(((rx - rx.mean()) ** 2).sum() * ((ry - ry.mean()) ** 2).sum())
        rho = float(d.sum() / den) if den > 0 else np.nan
        rows3.append({"industry": ind, "spearman": rho, "days": len(m)})
    r3 = pd.DataFrame(rows3).sort_values("spearman")
    if len(r3):
        print("\n== H3 行业：近5日涨停聚集度 vs 该行业未来5日收益（Spearman）==")
        print(f"  中位 r={r3['spearman'].median():+.3f} | "
              f"正相关的行业 {int((r3['spearman'] > 0).sum())}/{len(r3)}")
        for _, r in r3.head(5).iterrows():
            print(f"  {r['industry']:<6} r={r['spearman']:+.3f}")
        for _, r in r3.tail(3).iterrows():
            print(f"  {r['industry']:<6} r={r['spearman']:+.3f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    evdf.to_parquet(OUT_DIR / "limitup_events.parquet", index=False)
    if len(r3):
        r3.to_parquet(OUT_DIR / "limitup_industry_ic.parquet", index=False)
    print("\n>> 明细已存 research_results/")


if __name__ == "__main__":
    main()
