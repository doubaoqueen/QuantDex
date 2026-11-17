"""第 48 步：早盘/午后日内动量三关（D-088，用户 2026-11-18 修正后的进攻型假设）。

用户的策略（表弟规则的机器化，与本仓库此前检验过的两个格子本质不同）：
  ① 入场 = **早入场**：日内涨幅刚起来（离板尚远）就介入——不是 D-086 的尾盘追
     强势，也不是 D-087 的触板才买。机制假设：早盘动量多为消息/资金真实驱动，
     与尾盘拉升（做账/诱多，D-086 IC -0.109）行为可能不同。
  ② 出场 = **只持有一天**：T+1 无条件卖出（亏赚都走）。出口无参数 → 回测零
     过拟合空间。"达到预期提前卖"留作变体，先测纯版。

预注册配方（不扫参数）：
  检查点 10:30 / 14:00 两个（早盘定调 / 午后接力），信号 = 日内涨幅
  （检查点 bar 收盘 / 昨收 - 1）的截面 rank。介入价 = 检查点 bar 收盘价。
  可成交过滤：检查点已封板（一字/秒板）买不进 → 剔除；T+1 开盘跌停卖不出
  → 退化为 T+1 收盘卖（现实自适应）。

三关（39 号阈值原样，纪律与频率无关）：
  关 2：|IC| ≥ 0.03 且 NW-t ≥ 2（2 检查点 × 2 出场口径 = 4 检验，批内 BH-FDR）；
  关 3：分年方向一致 ≥ 6/7（2020-2026）且近 60 日不反向；
  组合关：Top50 每日等权（检查点介入 → T+1 卖），全成本 22.7bp/回合，vs 000300。
数据：分钟资产同 46/47（884 只 × 2020-01 起），聚合检查点收盘价入缓存。
"""
import json
import sys
import time as _t
from math import erfc, sqrt

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import st_symbol_set
from src.data.storage import ParquetStore

AGG_CACHE = STORE_DIR / "research_results" / "minute_ckpt_agg.parquet"
MIN_IC, MIN_T = 0.03, 2.0
MIN_YEARS = 6
TOP_N = 50
COST_RT = 0.00025 * 2 + 0.0005 * 2 + 0.0005 + 0.00001 * 2
CKPTS = {"ckpt_1030": pd.Timestamp("10:30").time(), "ckpt_1400": pd.Timestamp("14:00").time()}
EPS = 1e-6


def _limit_ratio(code: str) -> float:
    return 0.195 if code.startswith(("30", "68")) else 0.095


def aggregate_ckpt(store: ParquetStore) -> pd.DataFrame:
    """分钟 → 日频：每日检查点收盘价 + 开/收盘 + 昨收（连续性校验）。"""
    if AGG_CACHE.exists() and "--rebuild" not in sys.argv:
        return pd.read_parquet(AGG_CACHE)
    st = st_symbol_set(store)
    names = [n for n in store.list_names("minute") if n.split("__")[0] not in st]
    print(f">> 检查点聚合 {len(names)} 只...", flush=True)
    cal = pd.DatetimeIndex(sorted(pd.to_datetime(pd.read_parquet(
        STORE_DIR / "meta" / "trade_calendar.parquet", columns=["date"])["date"]).unique()))
    cal_pos = {d: i for i, d in enumerate(cal)}
    rows = []
    for i, nm in enumerate(names, 1):
        code = nm.split("__")[0]
        df = store.read("minute", nm, columns=["datetime", "open", "close", "volume"])
        df["dt"] = pd.to_datetime(df["datetime"])
        df["date"] = df["dt"].dt.normalize()
        df["t"] = df["dt"].dt.time
        day = pd.DataFrame({
            "day_open": df[df["t"] == pd.Timestamp("09:35").time()].groupby("date")["open"].first(),
            "day_close": df[df["t"] == pd.Timestamp("15:00").time()].groupby("date")["close"].first(),
        })
        for cname, tmark in CKPTS.items():
            day[cname] = df[df["t"] == tmark].groupby("date")["close"].first()
        day = day.dropna().reset_index()   # 四个价格齐才留（半天/断点日剔除）
        day["symbol"] = code
        day["dpos"] = day["date"].map(cal_pos)
        day["contig"] = (day["dpos"] - day["dpos"].shift(1)) == 1
        rows.append(day)
        if i % 200 == 0:
            print(f"   {i}/{len(names)}", flush=True)
    agg = pd.concat(rows, ignore_index=True)
    agg = agg.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = agg.groupby("symbol")
    agg["prev_close"] = g["day_close"].shift(1)
    prev_dpos = g["dpos"].shift(1)                       # 交易日序号差校验（停牌缺口不视为连续）
    agg["contig_prev"] = (agg["dpos"] - prev_dpos) == 1
    agg["date"] = pd.to_datetime(agg["date"])
    for cname in CKPTS:
        agg[f"gain_{cname}"] = agg[cname] / agg["prev_close"] - 1.0
    AGG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    agg.to_parquet(AGG_CACHE, index=False)
    print(f"   聚合缓存已落盘 {AGG_CACHE.name}（{len(agg)} 行，{_t.perf_counter()-t0:.0f}s）", flush=True)
    return agg


def prepare_panel(agg: pd.DataFrame, store: ParquetStore) -> pd.DataFrame:
    """PIT 过滤 + 介入/出场价格（涨跌停可成交过滤 + T+1 跌停出场退化）。"""
    pit = universe_load_pit(store)
    p = agg.merge(pit, on="symbol", how="inner")
    p = p[(p["date"] >= p["in_date"]) & (p["out_date"].isna() | (p["date"] < p["out_date"]))]
    p = p.drop(columns=["in_date", "out_date"]).sort_values(["symbol", "date"]).reset_index(drop=True)
    g = p.groupby("symbol")
    p["day_open_t1"] = g["day_open"].shift(-1)
    p["day_close_t1"] = g["day_close"].shift(-1)
    next_dpos = g["dpos"].shift(-1)
    p["contig_t1"] = (next_dpos - p["dpos"]) == 1
    p["prev_close_t1"] = p["day_close"]                     # T+1 的昨收 = T 日收盘
    lim = p["symbol"].map(_limit_ratio)
    # 介入过滤：检查点已封板（买不进）
    for cname in CKPTS:
        sealed_ckpt = p[cname] >= p["prev_close"] * (1 + lim) - EPS
        p[f"entry_{cname}"] = p[cname].where(~sealed_ckpt & p["contig_prev"])
        p[f"gain_{cname}"] = p[f"gain_{cname}"].where(~sealed_ckpt & p["contig_prev"])
    # 出场：T+1 开盘；开盘跌停 → 退化 T+1 收盘（现实自适应，非参数选择）
    limit_down_t1 = p["prev_close_t1"] * (1 - lim) + EPS
    exit_open_ok = (p["day_open_t1"] > limit_down_t1) & p["contig_t1"]
    p["exit_t1_open"] = p["day_open_t1"].where(exit_open_ok, p["day_close_t1"])
    p["exit_t1_close"] = p["day_close_t1"].where(p["contig_t1"])
    p["exit_fallback"] = (~exit_open_ok).astype(int)
    return p.sort_values(["date", "symbol"]).reset_index(drop=True)


def universe_load_pit(store):
    from src.data import universe
    pit = universe.load_pit_constituents("000300", store)
    pit["in_date"] = pd.to_datetime(pit["in_date"])
    pit["out_date"] = pd.to_datetime(pit["out_date"])
    return pit


def rankic(panel: pd.DataFrame, sig: str, fwd: str) -> pd.Series:
    d = panel[["date", sig, fwd]].dropna().copy()
    if len(d) < 100:
        return pd.Series(dtype=float)
    d["_f"] = d.groupby("date")[fwd].rank(pct=True)
    d["_s"] = d.groupby("date")[sig].rank(pct=True)
    d["_xy"], d["_s2"], d["_f2"] = d["_s"] * d["_f"], d["_s"] ** 2, d["_f"] ** 2
    gsum = d.groupby("date")[["_xy", "_s2", "_f2", "_s", "_f"]].sum()
    n = d.groupby("date")["_xy"].size()
    den = np.sqrt((gsum["_s2"] - gsum["_s"] ** 2 / n) * (gsum["_f2"] - gsum["_f"] ** 2 / n))
    ic = (gsum["_xy"] - gsum["_s"] * gsum["_f"] / n) / den
    return ic.replace([np.inf, -np.inf], np.nan).dropna()


def gates(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ck in CKPTS:
        sig = f"gain_{ck}"
        for exit_col, exit_name in (("exit_t1_open", "T+1开盘卖"), ("exit_t1_close", "T+1收盘卖")):
            fwd = f"fwd__{ck}__{exit_col}"
            entry = f"entry_{ck}"
            p2 = panel.copy()
            p2[fwd] = p2[exit_col] / p2[entry] - 1.0
            s = rankic(p2, sig, fwd)
            ic = float(s.mean()) if len(s) else np.nan
            t = float(_newey_west_tstat(s, 19)) if len(s) >= 20 else np.nan
            yy = s.groupby(s.index.year).mean().loc[lambda x: x.index >= 2020]
            n_ok = int((np.sign(yy) == np.sign(ic)).sum())
            near = float(s.iloc[-60:].mean()) if len(s) >= 60 else np.nan
            rows.append({"checkpoint": ck, "exit": exit_name, "days": int(len(s)),
                         "ic": ic, "nw_t": t, "years_ok": n_ok, "n_years": len(yy),
                         "ic_recent60": near,
                         "gate2": bool(abs(ic) >= MIN_IC and abs(t) >= MIN_T),
                         "gate3": bool(n_ok >= MIN_YEARS and np.sign(near) == np.sign(ic))})
            print(f"   {ck}/{exit_name}: IC {ic:+.4f} NW-t {t:+.2f} 分年 {n_ok}/{len(yy)}"
                  f" 近60日 {near:+.4f}", flush=True)
    out = pd.DataFrame(rows)
    out["p"] = [erfc(abs(v) / sqrt(2)) if pd.notna(v) else np.nan for v in out["nw_t"]]
    m = out["p"].notna().sum()
    pv = out.loc[out["p"].notna(), "p"].sort_values()
    out["q"] = (pv * m / pv.rank(method="first")).clip(upper=1.0).reindex(out.index)
    return out


def run_basket(panel: pd.DataFrame, ck: str, exit_col: str, bench: pd.Series,
               label: str) -> dict:
    """Top50 每日等权：检查点介入 → T+1 卖（表弟规则），全成本。"""
    entry = f"entry_{ck}"
    p = panel.dropna(subset=[entry, exit_col]).copy()
    p["r"] = p.groupby("date")[f"gain_{ck}"].rank(pct=True)
    top = p[p["r"] >= 1 - TOP_N / p.groupby("date")["r"].transform("size")]
    trade = top.copy()
    trade["ret"] = trade[exit_col] / trade[entry] - 1.0 - COST_RT
    realized = trade.assign(date=trade["date"] + pd.offsets.BDay(1))
    daily = realized.groupby("date")["ret"].mean().sort_index()
    nav = (1 + daily).cumprod()
    n = len(daily)
    exc = daily - bench.reindex(daily.index).fillna(0.0)
    m = {"days": n, "ann": float(nav.iloc[-1] ** (250 / n) - 1) if n else np.nan,
         "ir": float(exc.mean() / exc.std() * np.sqrt(250)) if exc.std() > 0 else np.nan,
         "mdd": float((nav / nav.cummax() - 1).min()), "total": float(nav.iloc[-1] - 1),
         "avg_members": float(realized.groupby("date").size().mean()),
         "fallback_pct": float(trade["exit_fallback"].mean())}
    yearly = {}
    for y, gg in daily.groupby(daily.index.year):
        nb = (1 + bench.reindex(gg.index).fillna(0.0)).cumprod()
        yearly[str(y)] = {"ret": float((1 + gg).prod() - 1), "bench": float(nb.iloc[-1] - 1)}
    m["yearly"] = yearly
    print(f"\n[{label}] 交易 {m['days']} 日 | 年化 {m['ann']:+.2%} | IR {m['ir']:+.2f}"
          f" | MDD {m['mdd']:+.2%} | 日均成员 {m['avg_members']:.0f}"
          f" | 跌停退化出场占比 {m['fallback_pct']:.1%}")
    for y, v in yearly.items():
        print(f"    {y}: {v['ret']:+.2%}（基准 {v['bench']:+.2%}）")
    return m


def main() -> None:
    store = ParquetStore()
    global t0
    t0 = _t.perf_counter()
    agg = aggregate_ckpt(store)
    print(f">> 聚合就绪 {len(agg)} 行")
    panel = prepare_panel(agg, store)
    print(f">> 面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，{len(panel)} 行")

    print("\n== 关 2+3：日内动量 IC（检查点介入 → 表弟出场）==")
    g = gates(panel)
    print(g[["checkpoint", "exit", "ic", "nw_t", "q", "years_ok",
             "ic_recent60", "gate2", "gate3"]].to_string(index=False))

    idx = store.read("daily", "000300__none", columns=["date", "close"])
    bench = (idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"]
             .pct_change())

    survivors = g[(g["gate2"]) & (g["gate3"])]
    result = {"gates": g.to_dict("records")}
    if len(survivors) == 0:
        print("\n== 裁决：三关全灭——早入场日内动量不建，进'已决策不做' ==")
        result["verdict"] = "rejected"
    else:
        print(f"\n幸存组合：{survivors[['checkpoint', 'exit']].to_dict('records')} → 组合关")
        for _, row in survivors.iterrows():
            ck, exit_col = row["checkpoint"], {"T+1开盘卖": "exit_t1_open",
                                               "T+1收盘卖": "exit_t1_close"}[row["exit"]]
            key = f"{ck}_{row['exit']}"
            result[key] = run_basket(panel, ck, exit_col, bench, key)
        net_ok = any(result[k].get("ir", -1) > 0 and result[k].get("ann", -1) > 0
                     for k in result if isinstance(result[k], dict) and "ir" in result[k])
        result["verdict"] = "passed" if net_ok else "passed_gross_only"
        if net_ok:
            print("\n== 组合关：扣全成本后为正 → 过闸 ==")
        else:
            print("\n== 组合关：扣全成本后为负 → 毛信号真实但不可收割 ==")
    _save(result)


def _save(result: dict) -> None:
    out = STORE_DIR / "research_results"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "intraday_momentum_gate.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
    print(f"已保存 {out / 'intraday_momentum_gate.json'}")
    try:
        record("intraday_momentum_gate",
               params={"checkpoints": list(CKPTS), "top_n": TOP_N, "cost_rt": COST_RT},
               metrics={f"{r['checkpoint']}/{r['exit']}": float(r["nw_t"])
                        for r in result["gates"]},
               notes=f"早入场日内动量三关裁决：{result['verdict']}",
               artifacts=["research_results/intraday_momentum_gate.json"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
