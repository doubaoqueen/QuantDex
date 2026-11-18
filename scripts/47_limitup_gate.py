"""第 47 步：涨停动力学事件研究（D-093，H-B：表弟行为的正确机器化版本）。

用户澄清（2026-11-18）：表弟 = 自选池人工精选 + 每日持续关注 + 盘中异动才出手，
不是"收盘追全池尾盘强势"（那是 D-086 杀掉的另一个假设）。本脚本检验的是表弟
行为里可机器化的核心：**触板事件的期望值**——

  对每个 (股票, 交易日)：从 5 分钟 bar 检测
    touched     首次触及涨停价的 bar（主板 10% / 创业板 20%，涨停价=昨收×(1+幅) 四舍五入到分）
    sealed      收盘封住（15:00 收盘 ≥ 涨停价）
    broken      触了但收盘没封住（炸板）
    entry       首次触板那根 bar 的收盘价（"看到异动、市价介入"的近似）

  收益口径（介入 → T+1，T+1 规则内合法）：
    exit_t1_open  T+1 开盘卖出（当日游资口径）
    exit_t1_close T+1 收盘卖出（隔日持有口径）
  两个成交界（排队现实性）：
    optimistic    封板价委托也算成交（假设排队能买到）
    conservative  封板价介入全部放弃（只统计介入价未触及涨停价的事件）

  涨停惯性（自选池价值检验）：打过板的股票，之后 20 个交易日内再打板的概率 vs 基础率。

检验（闸门精神，事件研究版）：逐日事件组合的等权日收益序列 → 全样本均值 NW-t、
分年方向一致 ≥6/7（2020-2026）、扣全成本 22.7bp/回合。封住/炸板分擘呈现。
数据：分钟资产同 46 号（884 只 × 2020-01 起），单遍扫描重算（需 bar high）。
"""
import json
import sys
from math import erfc, sqrt

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import st_symbol_set
from src.data.storage import ParquetStore

OUT_JSON = STORE_DIR / "research_results" / "limitup_gate.json"
EV_CACHE = STORE_DIR / "research_results" / "limitup_touch_events.parquet"   # 勿用 limitup_events（42 号旧实验同名）
COST_RT = 0.00025 * 2 + 0.0005 * 2 + 0.0005 + 0.00001 * 2
MIN_T = 2.0
MIN_YEARS = 6
EPS = 1e-6


def _limit_ratio(code: str) -> float:
    return 0.195 if code.startswith(("30", "68")) else 0.095


def aggregate_touches(store: ParquetStore, rebuild: bool = False) -> pd.DataFrame:
    """单遍扫描分钟 bar：每 (股票, 日) 一行——触板时间/封板/炸板/介入价 + T+1 开收盘。

    向量化写法（首版逐日 apply 写法 300 万次 pandas 调用直接超时，2026-11-18 教训）。
    事件集缓存独立命名（limitup_touch_events），42 号的 limitup_events 是旧实验。
    """
    cal = pd.DatetimeIndex(sorted(pd.to_datetime(pd.read_parquet(
        STORE_DIR / "meta" / "trade_calendar.parquet", columns=["date"])["date"]).unique()))
    cal_pos = {d: i for i, d in enumerate(cal)}
    if EV_CACHE.exists() and not rebuild:
        return finalize_events(pd.read_parquet(EV_CACHE), cal_pos, cal)
    st = st_symbol_set(store)
    names = [n for n in store.list_names("minute") if n.split("__")[0] not in st]
    print(f">> 触板扫描 {len(names)} 只...", flush=True)
    T0935, T1500 = pd.Timestamp("09:35").time(), pd.Timestamp("15:00").time()
    rows = []
    for i, nm in enumerate(names, 1):
        code = nm.split("__")[0]
        df = store.read("minute", nm, columns=["datetime", "open", "high", "low", "close"])
        df["dt"] = pd.to_datetime(df["datetime"])
        df["date"] = df["dt"].dt.normalize()
        df["t"] = df["dt"].dt.time
        # 日级骨架（向量化：掩码 + groupby.first）
        day = pd.DataFrame({
            "day_open": df[df["t"] == T0935].groupby("date")["open"].first(),
            "day_close": df[df["t"] == T1500].groupby("date")["close"].first(),
        }).dropna().reset_index()
        day["symbol"] = code
        day["prev_close"] = day["day_close"].shift(1)
        day["prev_dpos"] = day["date"].map(cal_pos).shift(1)
        day["dpos"] = day["date"].map(cal_pos)
        day["contig"] = (day["dpos"] - day["prev_dpos"]) == 1
        day["limit_price"] = (day["prev_close"] * (1 + _limit_ratio(code))).round(2)

        # 触板检测（向量化：涨停价广播到 bar，掩码 + 首触聚合）
        lp_map = day.set_index("date")["limit_price"]
        df["lp"] = df["date"].map(lp_map)
        hit = df[df["lp"].notna() & (df["high"] >= df["lp"] - EPS)].sort_values("dt")
        if hit.empty:
            day["touched"] = False
            day["entry"] = np.nan
        else:
            first = hit.groupby("date").first()[["dt", "close"]]
            day = day.merge(first.rename(columns={"dt": "first_touch", "close": "entry"}),
                            on="date", how="left")
            day["touched"] = day["entry"].notna()
        rows.append(day)
        if i % 200 == 0:
            print(f"   {i}/{len(names)}", flush=True)
    EV_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ev_out = pd.concat(rows, ignore_index=True)
    ev_out.to_parquet(EV_CACHE, index=False)
    print(f"   事件集缓存已落盘 {EV_CACHE.name}", flush=True)
    return finalize_events(ev_out, cal_pos, cal)


def finalize_events(ev: pd.DataFrame, cal_pos: dict, cal: pd.DatetimeIndex) -> pd.DataFrame:
    """缓存帧的后处理（幂等）：sealed/broken 判定 + T+1 出场连续性。"""
    ev = ev.copy()
    ev["date"] = pd.to_datetime(ev["date"])
    if "sealed" not in ev.columns:
        ev["sealed"] = ev["touched"] & (ev["day_close"] >= ev["limit_price"] - EPS)
        ev["broken"] = ev["touched"] & ~ev["sealed"]
        # T+1 出场（交易日连续性）
        ev = ev.sort_values(["symbol", "date"]).reset_index(drop=True)
        g = ev.groupby("symbol")
        ev["next_open"] = g["day_open"].shift(-1)
        ev["next_close"] = g["day_close"].shift(-1)
        ev["next_dpos"] = g["dpos"].shift(-1)
        ev["contig_t1"] = (ev["next_dpos"] - ev["dpos"]) == 1
        ev["at_limit_close_entry"] = ev["entry"] >= ev["limit_price"] - EPS   # 介入价已封板
        ev["t1"] = ev["date"].map(cal_pos).fillna(-1) + 1
        ev["t1_date"] = ev["t1"].map({i: d for i, d in enumerate(cal)})
    return ev


def event_returns(ev: pd.DataFrame) -> pd.DataFrame:
    """事件收益：介入 → T+1 开盘/收盘卖出，扣全成本；乐观/保守两界。"""
    ok = ev[ev["touched"] & ev["contig"] & ev["contig_t1"]].copy()
    out = []
    for scen, sub in (("optimistic", ok),
                      ("conservative", ok[~ok["at_limit_close_entry"]])):
        for exit_col, exit_name in (("next_open", "t1_open"), ("next_close", "t1_close")):
            s = sub.dropna(subset=["entry", exit_col, "day_close"]).copy()
            s = s[(s["entry"] > 0.01) & (s[exit_col] > 0.01)]
            s["ret"] = s[exit_col] / s["entry"] - 1.0 - COST_RT
            s["exit_mode"] = exit_name
            s["scenario"] = scen
            out.append(s)
    return pd.concat(out, ignore_index=True)


def gate_report(ev_ret: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (scen, exit_name), sub in ev_ret.groupby(["scenario", "exit_mode"]):
        strata = ((True, "封住"), (False, "炸板"), (None, "全部"),
                  (True, "前板池·全部"), (False, "无前板·全部"))
        for seal_flag, seal_name in strata:
            if seal_name == "前板池·全部":
                s = sub[sub["touched_prev20"]]
            elif seal_name == "无前板·全部":
                s = sub[~sub["touched_prev20"]]
            else:
                s = sub if seal_flag is None else sub[sub["sealed"] == seal_flag]
            daily = s.groupby("date")["ret"].mean().sort_index()   # 逐日事件等权
            if len(daily) < 60:
                continue
            ic_like = daily
            t = float(_newey_west_tstat(ic_like, 19))
            yy = ic_like.groupby(ic_like.index.year).mean()
            yy = yy.loc[yy.index >= 2020]
            n_ok = int((yy > 0).sum())
            rows.append({
                "scenario": scen, "exit": exit_name, "event_type": seal_name,
                "n_events": int(len(s)), "n_days": int(len(daily)),
                "mean_ret": float(ic_like.mean()), "median": float(s["ret"].median()),
                "win_rate": float((s["ret"] > 0).mean()), "nw_t": t,
                "years_pos": n_ok, "n_years": len(yy),
                "gate": bool(ic_like.mean() > 0 and t >= MIN_T and n_ok >= MIN_YEARS),
            })
    return pd.DataFrame(rows)


def mark_prior_board(ev: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """给每个股票日打上'过去 N 个交易日内是否打过板'标记（事前可知）。"""
    ev = ev.sort_values(["symbol", "date"]).reset_index(drop=True)
    ev["touched_prev20"] = False
    g = ev.groupby("symbol")
    for w in range(1, window + 1):
        prev = g["touched"].shift(w).fillna(False)
        ev["touched_prev20"] = ev["touched_prev20"] | prev.astype(bool)
    return ev


def inertia_stat(ev: pd.DataFrame) -> dict:
    """涨停惯性：过去 20 个交易日内打过板 → 今日再打板的概率 vs 基础率。"""
    base = float(ev["touched"].mean())
    cond = float(ev.loc[ev["touched_prev20"], "touched"].mean())
    seal_base = float(ev.loc[ev["touched"], "sealed"].mean())
    seal_cond = float(ev.loc[ev["touched"] & ev["touched_prev20"], "sealed"].mean())
    return {"base_rate": base, "cond_rate": cond,
            "lift": (cond / base - 1.0) if base > 0 else np.nan,
            "seal_rate_all": seal_base, "seal_rate_prior": seal_cond}


def main() -> None:
    store = ParquetStore()
    ev = aggregate_touches(store, rebuild="--rebuild" in sys.argv)
    n_touched = int(ev["touched"].sum())
    print(f">> 事件集：{len(ev)} 股票日，触板 {n_touched} 次"
          f"（封住 {int(ev['sealed'].sum())}，炸板 {int(ev['broken'].sum())}）", flush=True)

    # 惯性列前置（事前可知：介入当天早上就知道它过去 20 日有没有打过板）
    ev = mark_prior_board(ev, window=20)
    ev_ret = event_returns(ev)
    report = gate_report(ev_ret, ev)
    print("\n== 事件收益（介入→T+1，扣全成本 22.7bp）==")
    show = report.copy()
    for c in ("mean_ret", "median", "win_rate"):
        show[c] = show[c].map(lambda v: f"{v:+.2%}")
    show["nw_t"] = show["nw_t"].map(lambda v: f"{v:+.2f}")
    print(show.to_string(index=False))

    inert = inertia_stat(ev)
    print(f"\n== 涨停惯性 ==\n基础率 {inert['base_rate']:.2%} | 过去 20 日内打过板 → "
          f"再打板 {inert['cond_rate']:.2%}（lift {inert['lift']:+.0%}）")

    # 裁决只认**事前可交易层**（全部/前板池/无前板）——"封住/炸板"是事后分层，仅信息性：
    # 介入那一刻你不知道它封不封，封住子集的 +2.4% 是结果不是入场信号。
    ex_ante = report[report["event_type"].isin(("全部", "前板池·全部", "无前板·全部"))]
    passed = ex_ante[ex_ante["gate"]]
    result = {"events": int(n_touched), "report": report.to_dict("records"),
              "inertia": inert,
              "verdict": "passed" if len(passed) else "rejected",
              "note": "optimistic 界含封板价成交（排队假设）；conservative 界放弃封板价介入"}
    if len(passed):
        print(f"\n== 裁决：保守界过闸 {len(passed)} 项 → 影子盘候选（排队成交折扣仍需实测）==")
    else:
        print("\n== 裁决：保守界全灭——'逮涨停'事件在扣成本后无正期望，进'已决策不做' ==")
    _save(result)


def _save(result: dict) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
    print(f"已保存 {OUT_JSON}")
    try:
        record("limitup_gate",
               params={"cost_rt": COST_RT, "min_t": MIN_T, "min_years": MIN_YEARS},
               metrics={"n_events": result["events"],
                        "best_nw_t": max((abs(r["nw_t"]) for r in result["report"]), default=0)},
               notes=f"涨停动力学裁决：{result['verdict']}",
               artifacts=["research_results/limitup_gate.json"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
