"""第 55 步：涨停 dip-buy 事件扫描的严格时间外推（D-118 重写版）。

⚠️ 本脚本 2026-11-21 首版（D-103）的三处实现与预注册规则不符，归档结论因此建立在
错误证据上（审计见 D-118）：
  ① 涨停价用 9.5%/19.5% 缓冲口径 → 155/369 的"触板"是伪事件（最高价落在 9.5%~10%）；
  ② "T-1 换手 < 3%"从分钟线读，而该列全空 → 求和为 0 → 过滤恒通过（等于没过滤）；
  ③ "T+1 开盘无条件卖"实际实现成 -9%/+14% 止损止盈——归档中位数 -9.20% 就是止损线。
本版把三条规则下沉到 src/data/limitup.py（带单元测试），脚本只做编排。

预注册规则（D-103 原文，字段级时点全部事前可知）：
  触板     bar.high >= round(前收 × (1+幅度), 2)，幅度主板 10% / 创科 20%
  首触     当日第一根触板 bar 的时间 <= 10:00
  非一字   当日开盘价 < 涨停价（一字板买不到）
  低换手   **前一日**全天换手 < 3%（当日换手要到收盘才知道，不能用）
  介入     首触之后（含首触 bar）第一根收盘 < 涨停价的 bar，按该 bar 收盘价成交
  出场     T+1 开盘无条件卖出（T+1 开盘跌停 → 退化为 T+1 收盘）
  成本     20.2bp/回合

裁决（预注册）：混合口径（回封 + 炸板都算，事前不知道回不回封）样本外 2024-26
  净均值 > 0 且逐日 NW-t >= 2 → 通过；否则关闭。
股票池：沪深300 历史成分并集，按成员期 + 按日 ST 状态过滤（与因子类检验同口径）。

用法：python scripts/55_limitup_oos_strict.py
产出：research_results/limitup_oos_strict.json + limitup_dip_events.parquet
"""
import json

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.factors import st_symbol_set
from src.data import universe
from src.data.limitup import (
    COST_RT,
    FIRST_TOUCH_DEADLINE,
    LOW_TURNOVER_PCT,
    cluster_t,
    scan_universe,
    summarize,
)
from src.data.storage import ParquetStore

OUT_JSON = STORE_DIR / "research_results" / "limitup_oos_strict.json"
OUT_EVENTS = STORE_DIR / "research_results" / "limitup_dip_events.parquet"
START = "2020-01-01"


def _rule_card() -> str:
    """把预注册规则的字段级时点打出来（D-102 教训：事前可知是字段级属性）。"""
    return (
        f"触板=真实涨停价(按分四舍五入) | 首触<={FIRST_TOUCH_DEADLINE} | 非一字 | "
        f"T-1 换手<{LOW_TURNOVER_PCT}% | 开板确认价介入 | T+1 开盘卖 | 成本 {COST_RT * 1e4:.1f}bp"
    )


def _self_check(ev: pd.DataFrame, store: ParquetStore) -> list[str]:
    """启动前的口径自检（防"实现与预注册不符"再次发生）。

    逐条验证**字段语义**而不只是"跑通"（抽样 30 起回读原始数据对照）：
      ① 出场价必须是 T+1 的开盘价（跌停退化时是 T+1 收盘）；
      ② 介入价必须低于当日真实涨停价，涨停价必须等于 round(前收×(1+幅度), 2)；
      ③ t1_turnover 必须等于**前一交易日**的日线换手率（当日换手是未来函数）。
    """
    from src.data.limitup import get_minute, limit_ratio, turnover_by_date

    issues = []
    if len(ev) == 0:
        return ["事件集为空"]
    s = ev.head(30)
    bad_exit = bad_entry = bad_turn = 0
    for r in s.itertuples():
        m = get_minute(store, r.symbol)
        m["dt"] = pd.to_datetime(m["datetime"])
        m["date"] = m["dt"].dt.normalize()
        nxt = m[m["date"] == pd.Timestamp(r.exit_date)]
        if r.exit_fallback:
            expect = float(nxt["close"].iloc[-1]) if len(nxt) else None
        else:
            expect = float(nxt["open"].iloc[0]) if len(nxt) else None
        if expect is not None and abs(expect - float(r.exit_price)) > 1e-6:
            bad_exit += 1
        if float(r.entry) >= float(r.limit_price) - 0.005:
            bad_entry += 1
        prev = m[m["date"] < pd.Timestamp(r.date)]
        if len(prev):
            pc = float(prev["close"].iloc[-1])
            if abs(round(pc * (1 + limit_ratio(r.symbol)), 2) - float(r.limit_price)) > 1e-6:
                bad_entry += 1
        if pd.notna(r.t1_turnover):
            turn = turnover_by_date(store, r.symbol)
            entry_day = pd.Timestamp(r.date).strftime("%Y-%m-%d")
            prev_days = [d for d in turn if d < entry_day]
            want = turn[max(prev_days)] if prev_days else None      # 前一日，不是当日
            if want is not None and abs(float(want) - float(r.t1_turnover)) > 1e-6:
                bad_turn += 1
    if bad_exit:
        issues.append(f"出场价与 T+1 开盘不符 {bad_exit}/30")
    if bad_entry:
        issues.append(f"介入价/涨停价口径不符 {bad_entry}/30")
    if bad_turn:
        issues.append(f"t1_turnover 与日线前一日换手不符 {bad_turn}/30")
    return issues


def main() -> None:
    store = ParquetStore()
    print(f">> 预注册规则：{_rule_card()}", flush=True)
    pit = universe.load_pit_constituents("000300", store)
    st_flag = store.read_or_empty("meta", "st_flag")
    if st_flag.empty:
        print(">> [warn] 无按日 ST 标记表（meta/st_flag）——仅按今日名单剔除 ST，"
              "请先跑 scripts/61_fetch_st_flag.py", flush=True)
        today_st = st_symbol_set(store)
        st_flag = None
    else:
        today_st = None
    names = [f for f in sorted((STORE_DIR / "market" / "minute").glob("*__5__none.parquet"))]
    codes = [f.name.split("__")[0] for f in names]
    if today_st:
        codes = [c for c in codes if c not in today_st]
    print(f">> 扫描 {len(codes)} 只（PIT 成员期 + 按日 ST 过滤）...", flush=True)
    ev_all = scan_universe(store, codes, start=START, pit=pit, st_flag=st_flag)
    print(f"   原始开板确认信号 {len(ev_all)}（未过滤换手）", flush=True)
    if ev_all.empty:
        _save({"verdict": "NO_EVENTS", "rule": _rule_card()})
        return
    ev = ev_all[ev_all["t1_turnover"] < LOW_TURNOVER_PCT].reset_index(drop=True)
    print(f"   T-1 换手 < {LOW_TURNOVER_PCT}%：{len(ev)} 起"
          f"（T-1 换手中位 {ev_all['t1_turnover'].median():.2f}%）", flush=True)
    ev["entry_year"] = pd.to_datetime(ev["date"]).dt.year
    issues = _self_check(ev, store)
    for m in issues:
        print(f"   ⚠️ 自检：{m}", flush=True)

    res = summarize(ev)
    print("\n== 预注册规则下的净期望（T+1 开盘卖，扣 20.2bp）==")
    for label, m in res.items():
        if m.get("insufficient"):
            print(f"  {label}: n={m['n']}（不足）")
            continue
        print(f"  {label}: n={m['n']} 日 {m['dates']} 净均值 {m['mean']:+.2%} "
              f"中位 {m['median']:+.2%} 胜率 {m['win']:.0%} | 逐日 NW-t {m['nw_t_daily']:+.2f} "
              f"| 按日聚类 t {cluster_t(_slice(ev, label)):+.2f}", flush=True)
    print("\n  逐年：")
    yr = ev.groupby("entry_year")["net"].agg(["size", "mean", "median"])
    for y, r in yr.iterrows():
        print(f"    {int(y)}: n={int(r['size']):>4} 净均值 {r['mean']:+.2%} 中位 {r['median']:+.2%}")
    print("\n  事后分层（仅信息性，不可交易）：")
    for lab, sub in (("收盘封住", ev[ev["sealed"]]), ("收盘炸板", ev[~ev["sealed"]])):
        if len(sub):
            print(f"    {lab}: n={len(sub)} 净均值 {sub['net'].mean():+.2%} "
                  f"中位 {sub['net'].median():+.2%}")
    print(f"  扣费前毛均值 {ev['gross'].mean():+.2%}（成本 {COST_RT:.4%}）")

    oos = res.get("样本外 2024-26", {})
    passed = (oos.get("mean", -1) > 0 and oos.get("nw_t_daily", 0) >= 2 and not issues)
    verdict = "OOS_PASS" if passed else "CLOSED"
    print(f"\n== 裁决：{verdict} ==")
    if passed:
        print("→ 样本外净期望为正且 NW-t>=2 → 小资金卫星仓候选（容量与尾部建模另做）")
    else:
        print("→ 样本外无正期望 → 按预注册关闭；结论为「沪深300 池 + 追涨类」不可做，"
              "换池或换族需另立假设")
    ev.drop(columns=["entry_year"]).to_parquet(OUT_EVENTS, index=False)
    _save({"verdict": verdict, "rule": _rule_card(), "cost_rt": COST_RT,
           "n_all_signals": int(len(ev_all)), "n_after_turnover": int(len(ev)),
           "self_check_issues": issues,
           "summary": res, "gross_mean": float(ev["gross"].mean()),
           "by_year": {str(int(k)): {"n": int(v["size"]), "mean": float(v["mean"]),
                                     "median": float(v["median"])}
                       for k, v in yr.iterrows()}})
    try:
        record("limitup_oos_strict",
               params={"rule": _rule_card(), "start": START, "turnover_max": LOW_TURNOVER_PCT},
               metrics={"oos_mean": float(oos.get("mean", float("nan"))),
                        "oos_nw_t": float(oos.get("nw_t_daily", float("nan")))},
               notes=f"D-118 重写版 dip-buy OOS：{verdict}", artifacts=[OUT_JSON.name])
    except Exception as e:  # noqa: BLE001  存档失败不掩盖结论
        print(f"[experiments] 记录失败: {e}")


def _slice(ev: pd.DataFrame, label: str) -> pd.DataFrame:
    lo, hi = {"全样本 2020-26": (2020, 2026), "样本内 2020-23": (2020, 2023),
              "样本外 2024-26": (2024, 2026)}.get(label, (2020, 2026))
    return ev[ev["entry_year"].between(lo, hi)]


def _save(payload: dict) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(f"已保存 {OUT_JSON}")


if __name__ == "__main__":
    main()
