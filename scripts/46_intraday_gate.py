"""第 46 步：尾盘动量日内三关（D-092，用户 2026-11-18 提案"机器盯盘逮涨停"的假设化）。

假设（H-A，配方冻结，参数不扫）：
  尾盘强势有隔夜/次日延续——"表弟盯盘逮涨停"的机器版 = 每日 14:30 后扫描全池。
  信号（T 日，全部只用 T 日及以前数据，无前视）：
    TAIL_RET = 尾盘 30 分钟收益（14:30 收盘价 → 15:00 收盘价，5 分钟 bar）
    VOL_TAIL = 尾盘量能占比异常（尾盘 6 根 bar 成交量占比 / 12.5% 基准）

可成交口径（分钟级版 D-038，两个预设 harvest 窗口，T+1 规则内合法）：
  fwd_gap  = T 收盘竞价买入 → T+1 开盘卖出（隔夜跳空，legal：T 买 T+1 卖）
  fwd_next = T+1 开盘买入 → T+2 开盘卖出（次日全场，legal：T+1 买 T+2 卖）
  入场涨跌停可成交过滤：收盘竞价买入时 T 收盘触及涨停价 → 剔除；
  T+1 开盘买入时开盘即涨停（一字板/秒板）→ 剔除。主板 10%/创业板 20% 分板计算。

三关（39 号阈值原样照搬——纪律与频率无关）：
  关 2 强度：|IC| ≥ 0.03 且 NW-t ≥ 2（批内 BH-FDR，2 因子 × 2 窗口 = 4 检验）；
  关 3 稳定性：分年 IC 方向一致 ≥ 6/7 年（2020-2026）且近 60 日不反向；
  组合关：Top50 每日等权（收盘竞价口径与 T+1 开盘口径各跑一版），
          全成本（佣金/印花/滑点/过户），vs 000300 基准。

数据：分钟聚合缓存 research_results/minute_daily_agg.parquet（916 只 × 2020-01 起）。
裁决：过闸 → 尾盘策略进影子盘候选（注册表+日内监控原型另议）；不过 → 已决策不做。
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
from src.data import universe
from src.data.storage import ParquetStore

AGG_CACHE = STORE_DIR / "research_results" / "minute_daily_agg.parquet"
MIN_IC, MIN_T = 0.03, 2.0
MIN_YEARS = 6
TOP_N = 50
COST_RT = 0.00025 * 2 + 0.0005 * 2 + 0.0005 + 0.00001 * 2   # 双边全成本 ≈ 22.7bp


def _limit_ratio(code: str) -> float:
    """涨跌停幅度：创业板(30/68) 20%，主板 10%（000300 成分无北交所）。"""
    return 0.195 if code.startswith(("30", "68")) else 0.095


def aggregate_minute(store: ParquetStore) -> pd.DataFrame:
    """分钟 → 日频聚合：每标的一行/日（开盘/收盘/全天量/尾盘收益/尾盘量占比）。"""
    st = st_symbol_set(store)
    names = [n for n in store.list_names("minute") if n.split("__")[0] not in st]
    print(f">> 分钟聚合 {len(names)} 只（ST 剔除后）...", flush=True)
    rows = []
    for i, nm in enumerate(names, 1):
        code = nm.split("__")[0]
        df = store.read("minute", nm, columns=["datetime", "open", "close", "volume"])
        df["dt"] = pd.to_datetime(df["datetime"])
        df["date"] = df["dt"].dt.normalize()
        df["t"] = df["dt"].dt.time
        t_open = df[df["t"] == pd.Timestamp("09:35").time()]
        t_close = df[df["t"] == pd.Timestamp("15:00").time()]
        t_1430 = df[df["t"] == pd.Timestamp("14:30").time()]
        tail = df[(df["t"] > pd.Timestamp("14:30").time())
                  & (df["t"] <= pd.Timestamp("15:00").time())]
        if t_open.empty or t_close.empty:
            continue
        day = pd.DataFrame({
            "date": t_close["date"].to_numpy(),
            "symbol": code,
            "day_open": t_open.set_index("date")["open"].reindex(t_close["date"]).to_numpy(),
            "day_close": t_close["close"].to_numpy(),
            "close_1430": t_1430.set_index("date")["close"].reindex(t_close["date"]).to_numpy(),
            "tail_ret": (t_close.set_index("date")["close"]
                         / t_1430.set_index("date")["close"].reindex(t_close["date"]) - 1.0).to_numpy(),
            "vol_day": df.groupby("date")["volume"].sum().reindex(t_close["date"]).to_numpy(),
        })
        tailv = tail.groupby("date")["volume"].sum()
        day["vol_tail"] = tailv.reindex(day["date"]).to_numpy()
        rows.append(day)
        if i % 150 == 0:
            print(f"   {i}/{len(names)}", flush=True)
    agg = pd.concat(rows, ignore_index=True)
    agg["vol_ratio"] = agg["vol_tail"] / (agg["vol_day"] * 0.125)   # 尾盘占比 / 12.5% 基准
    agg["date"] = pd.to_datetime(agg["date"])
    return agg


def get_agg(store: ParquetStore, rebuild: bool = False) -> pd.DataFrame:
    if AGG_CACHE.exists() and not rebuild:
        return pd.read_parquet(AGG_CACHE)
    agg = aggregate_minute(store)
    AGG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    agg.to_parquet(AGG_CACHE, index=False)
    print(f"   聚合缓存已落盘 {AGG_CACHE.name}（{len(agg)} 行）", flush=True)
    return agg


def prepare_panel(agg: pd.DataFrame, store: ParquetStore) -> pd.DataFrame:
    """PIT 过滤 + 信号与前瞻收益（带交易日连续性校验 + 涨跌停可成交标记）。"""
    pit = universe.load_pit_constituents("000300", store)
    pit["in_date"] = pd.to_datetime(pit["in_date"])
    pit["out_date"] = pd.to_datetime(pit["out_date"])
    p = agg.merge(pit, on="symbol", how="inner")
    p = p[(p["date"] >= p["in_date"]) & (p["out_date"].isna() | (p["date"] < p["out_date"]))]
    p = p.drop(columns=["in_date", "out_date"]).sort_values(["symbol", "date"]).reset_index(drop=True)

    all_dates = pd.DatetimeIndex(np.sort(p["date"].unique()))
    tno = {d: i for i, d in enumerate(all_dates)}
    p["_tno"] = p["date"].map(tno)
    g = p.groupby("symbol")
    for col in ("day_close", "day_open"):
        p[f"prev_{col}"] = g[col].shift(1)
        p[f"{col}_t1"] = g[col].shift(-1)
        p[f"{col}_t2"] = g[col].shift(-2)
    p["tno_t1"] = g["_tno"].shift(-1)
    p["tno_t2"] = g["_tno"].shift(-2)

    # 市场尾盘收益（截面均值；对 rank 无影响，仅口径记录）
    p["mkt_tail"] = p.groupby("date")["tail_ret"].transform("mean")
    p["sig_tail"] = p["tail_ret"]            # 信号 1：尾盘动量（截面 rank 用）
    p["sig_vol"] = p["vol_ratio"]            # 信号 2：尾盘量能异常

    # 可成交前瞻收益：连续性校验（跨洞/停牌置 NaN）+ 涨停不可买过滤
    cont1 = (p["tno_t1"] - p["_tno"]) == 1
    cont2 = (p["tno_t2"] - p["_tno"]) == 2
    lim = p["symbol"].map(_limit_ratio)
    at_limit_close = p["day_close"] >= p["prev_day_close"] * (1 + lim) - 1e-9   # T 收盘封板
    at_limit_open_t1 = p["day_open_t1"] >= p["day_close"] * (1 + lim) - 1e-9   # T+1 开盘涨停
    p["fwd_gap"] = ((p["day_open_t1"] / p["day_close"] - 1.0)
                    .where(cont1 & ~at_limit_close)
                    .replace([np.inf, -np.inf], np.nan))
    p["fwd_next"] = ((p["day_open_t2"] / p["day_open_t1"] - 1.0)
                     .where(cont2 & ~at_limit_open_t1)
                     .replace([np.inf, -np.inf], np.nan))
    return p.drop(columns=["_tno", "tno_t1", "tno_t2"]).sort_values(
        ["date", "symbol"]).reset_index(drop=True)


def rankic(panel: pd.DataFrame, sig: str, fwd: str) -> pd.Series:
    d = panel[["date", sig, fwd]].dropna().copy()
    d["_f"] = d.groupby("date")[fwd].rank(pct=True)
    d["_s"] = d.groupby("date")[sig].rank(pct=True)
    d["_xy"] = d["_s"] * d["_f"]
    d["_s2"] = d["_s"] ** 2
    d["_f2"] = d["_f"] ** 2
    g = d.groupby("date")[["_xy", "_s2", "_f2", "_s", "_f"]].sum()
    n = d.groupby("date")["_xy"].size()
    den = np.sqrt((g["_s2"] - g["_s"] ** 2 / n) * (g["_f2"] - g["_f"] ** 2 / n))
    ic = (g["_xy"] - g["_s"] * g["_f"] / n) / den
    return ic.replace([np.inf, -np.inf], np.nan).dropna()


def gates(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sig in ("sig_tail", "sig_vol"):
        for fwd in ("fwd_gap", "fwd_next"):
            s = rankic(panel, sig, fwd)
            ic = float(s.mean())
            t = float(_newey_west_tstat(s, 19))
            yy = s.groupby(s.index.year).mean()
            yy = yy.loc[yy.index >= 2020]
            n_ok = int((np.sign(yy) == np.sign(ic)).sum())
            near = float(s.iloc[-60:].mean()) if len(s) >= 60 else np.nan
            rows.append({"signal": sig, "window": fwd, "days": int(len(s)),
                         "ic": ic, "nw_t": t, "years_ok": n_ok, "n_years": len(yy),
                         "ic_recent60": near,
                         "gate2": bool(abs(ic) >= MIN_IC and abs(t) >= MIN_T),
                         "gate3": bool(n_ok >= MIN_YEARS and np.sign(near) == np.sign(ic))})
            print(f"   {sig}/{fwd}: IC {ic:+.4f} NW-t {t:+.2f} 分年 {n_ok}/{len(yy)}"
                  f" 近60日 {near:+.4f}", flush=True)
    out = pd.DataFrame(rows)
    out["p"] = [erfc(abs(v) / sqrt(2)) if pd.notna(v) else np.nan for v in out["nw_t"]]
    m = out["p"].notna().sum()
    pv = out.loc[out["p"].notna(), "p"].sort_values()
    out["q"] = (pv * m / pv.rank(method="first")).clip(upper=1.0).reindex(out.index)
    return out


def run_daily_basket(panel: pd.DataFrame, combo: list[tuple[str, int]], mode: str,
                     bench: pd.Series, label: str) -> dict:
    """Top50 每日等权高频换手盘子（IC 符号方向合成，向量化账本，全成本）。mode: close_auction | open_t1。"""
    p = panel.dropna(subset=[c[0] for c in combo]).copy()
    sc = None
    for sig, direction in combo:
        r = p.groupby("date")[sig].rank(pct=True)
        rr = r if direction > 0 else 1.0 - r
        sc = rr if sc is None else sc + rr
    p["score"] = sc / len(combo)
    top = p.sort_values(["date", "score"], ascending=[True, False]).groupby("date").head(TOP_N)   # 精确 TopN（阈值式在边界多选一名）
    if mode == "close_auction":
        trade = top.dropna(subset=["fwd_gap"]).copy()
        trade["ret"] = trade["fwd_gap"] - COST_RT          # T 收盘竞价买 → T+1 开盘卖
        realized = trade.assign(date=trade["date"] + pd.offsets.BDay(1))
    else:
        trade = top.dropna(subset=["fwd_next"]).copy()
        trade["ret"] = trade["fwd_next"] - COST_RT          # T+1 开盘买 → T+2 开盘卖
        realized = trade.assign(date=trade["date"] + pd.offsets.BDay(2))
    daily = realized.groupby("date")["ret"].mean()
    daily = daily.sort_index()
    nav = (1 + daily).cumprod()
    n = len(daily)
    exc = daily - bench.reindex(daily.index).fillna(0.0)
    m = {
        "days": n, "ann": float(nav.iloc[-1] ** (250 / n) - 1) if n else np.nan,
        "ir": float(exc.mean() / exc.std() * np.sqrt(250)) if exc.std() > 0 else np.nan,
        "mdd": float((nav / nav.cummax() - 1).min()), "total": float(nav.iloc[-1] - 1),
        "avg_members": float(realized.groupby("date").size().mean()),
        "cost_drag_ann": float(COST_RT * 250),
    }
    yearly = {}
    for y, gg in daily.groupby(daily.index.year):
        nb = (1 + bench.reindex(gg.index).fillna(0.0)).cumprod()
        yearly[str(y)] = {"ret": float((1 + gg).prod() - 1), "bench": float(nb.iloc[-1] - 1)}
    m["yearly"] = yearly
    print(f"\n[{label}] 交易 {m['days']} 日 | 年化 {m['ann']:+.2%} | IR {m['ir']:+.2f}"
          f" | MDD {m['mdd']:+.2%} | 年成本拖累 {m['cost_drag_ann']:.1%}"
          f" | 日均成员 {m['avg_members']:.0f}")
    for y, v in yearly.items():
        print(f"    {y}: {v['ret']:+.2%}（基准 {v['bench']:+.2%}）")
    return m


def main() -> None:
    rebuild = "--rebuild" in sys.argv
    store = ParquetStore()
    t0 = _t.perf_counter()
    agg = get_agg(store, rebuild=rebuild)
    print(f">> 聚合就绪 {len(agg)} 行（{_t.perf_counter()-t0:.0f}s）")
    panel = prepare_panel(agg, store)
    print(f">> 面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，{len(panel)} 行")

    print("\n== 关 2+3：尾盘动量 IC（两个可成交窗口，批内 FDR）==")
    g = gates(panel)
    print(g[["signal", "window", "ic", "nw_t", "q", "years_ok", "ic_recent60",
             "gate2", "gate3"]].to_string(index=False))

    idx = store.read("daily", "000300__none", columns=["date", "close"])
    bench = (idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"]
             .pct_change())

    result = {"gates": g.to_dict("records")}
    survivors = g[(g["gate2"]) & (g["gate3"])]
    if len(survivors) == 0:
        print("\n== 裁决：三关全灭——尾盘动量日内策略不建，进'已决策不做' ==")
        result["verdict"] = "rejected"
    else:
        # 方向按各窗口 IC 符号揭示（项目惯例：值大值小好由 IC 符号决定，D-042 不扫方向）
        def combo_for(window: str) -> list[tuple[str, int]]:
            sub = survivors[survivors["window"] == window]
            return [(r.signal, 1 if r.ic > 0 else -1) for r in sub.itertuples(index=False)]

        c1, c2 = combo_for("fwd_gap"), combo_for("fwd_next")
        print(f"\n幸存组合（收盘竞价窗口）：{c1}")
        print(f"幸存组合（T+1 开盘窗口）：{c2}")
        result["survivors"] = sorted(survivors["signal"].unique().tolist())
        r1 = run_daily_basket(panel, c1, "close_auction", bench, "尾盘竞价口径") if c1 else None
        r2 = run_daily_basket(panel, c2, "open_t1", bench, "T+1 开盘口径") if c2 else None
        if r1:
            result["close_auction"] = r1
        if r2:
            result["open_t1"] = r2
        net_ok = any(m and m["ir"] > 0 and m["ann"] > 0
                     for m in (result.get("close_auction"), result.get("open_t1")))
        result["verdict"] = "passed" if net_ok else "passed_gross_only"
        if net_ok:
            print("\n== 组合关：扣全成本后为正 → 过闸（方向为 IC 揭示的形态，未必是'追强势'）==")
        else:
            print("\n== 组合关：扣全成本后为负 → 毛信号真实但不可收割，进'已决策不做' ==")
    _save(result)


def _save(result: dict) -> None:
    out = STORE_DIR / "research_results"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "offensive_intraday_gate.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
    print(f"已保存 {out / 'offensive_intraday_gate.json'}")
    try:
        record("offensive_intraday_gate",
               params={"signals": ["TAIL_RET", "VOL_TAIL"], "top_n": TOP_N,
                       "cost_rt": COST_RT},
               metrics={f"{r['signal']}/{r['window']}": float(r["nw_t"])
                        for r in result["gates"]},
               notes=f"尾盘动量日内三关裁决：{result['verdict']}",
               artifacts=["research_results/offensive_intraday_gate.json"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
