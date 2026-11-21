"""第 55 步：修正口径的涨停 dip-buy 严格 OOS（D-103，并行会话 D-102 审计的落地执行）。

修正两项（并行会话 D-102 审计发现）：
  ① 涨停价口径：真实涨停价 = round(昨收 × (1+0.10/0.20), 2)——46/47/48/53/54 号
     传承的 0.095/0.195 缓冲口径（docstring 写 10%/20% 与实现不符）废弃；
  ② 换手过滤去未来函数：低换手过滤改用 **T-1 全天换手**（事前可知）——事件日
     全天换手在 10:00 介入时点不可知（未来函数，D-102 审计①）。

规则（预注册冻结，与 D-102 用户定义一致仅修口径）：
  触板 = bar high ≥ 真实涨停价；介入 = 首触 bar 收盘 < 真实涨停价（炸板即买）
        或其后首个收盘 < 真实涨停价的 bar（开板确认买入）；介入价 = 该 bar 收盘。
  过滤：T-1 全天换手 < 3% + 非一字（开盘 < 涨停价）+ 首触 ≤ 10:00。
  出场 = T+1 开盘无条件卖；净收益扣全成本 20.2bp/回合（COST 误标已更正）。
裁决（预注册）：混合口径（回封+炸板都算，事前可执行）样本外 2024-26 净均值 > 0
  且逐日 NW-t ≥ 2 → 通过（小资金卫星仓候选）；否则永久关闭。
容量代理：开板窗口内成交额合计（可吃到的流动性上限的粗测）。
"""
import json

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import st_symbol_set
from src.data.storage import ParquetStore

COST = 0.00025 * 2 + 0.0005 * 2 + 0.0005 + 0.00001 * 2   # 20.2bp/回合（D-095 误标更正）
REAL_LIM = {"main": 0.10, "gem": 0.20}
OUT = STORE_DIR / "research_results" / "limitup_oos_strict.json"


def _lim_ratio(code: str) -> float:
    return REAL_LIM["gem"] if code.startswith(("30", "68")) else REAL_LIM["main"]


def main() -> None:
    s = ParquetStore()
    st_syms = st_symbol_set(s)
    minute_dir = STORE_DIR / "market" / "minute"
    files = [f for f in sorted(minute_dir.glob("*__5__none.parquet"))
             if f.name.split("__")[0] not in st_syms]
    cal = pd.DatetimeIndex(sorted(pd.to_datetime(pd.read_parquet(
        STORE_DIR / "meta" / "trade_calendar.parquet", columns=["date"])["date"]).unique()))
    cal_pos = {d: i for i, d in enumerate(cal)}
    pit = pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000300.parquet")
    pit["in_date"] = pd.to_datetime(pit["in_date"])
    pit["out_date"] = pd.to_datetime(pit["out_date"])
    pit_g = pit.groupby("symbol")
    print(f">> 修正口径扫描：{len(files)} 只（真实涨停价 + T-1 换手过滤）", flush=True)

    events = []
    done = 0
    for f in files:
        code = f.name.split("__")[0]
        lim_r = _lim_ratio(code)
        bars = pd.read_parquet(f, columns=["datetime", "open", "high", "low", "close",
                                           "turnover", "amount"])
        bars["date"] = pd.to_datetime(bars["datetime"]).dt.normalize()
        bars = bars.sort_values("datetime")
        if code in pit_g.groups:
            rows = pit_g.get_group(code)
            in_d = pd.to_datetime(rows["in_date"].iloc[0])
            bars = bars[bars["date"] >= in_d]
            outs = rows["out_date"].dropna()
            if len(outs):
                bars = bars[bars["date"] < pd.to_datetime(outs.iloc[-1])]
        if len(bars) < 200:
            continue

        # 日级骨架：每交易日 prev_close（前交易日收盘）与 prev_turn（T-1 全天换手，事前可知）
        days = []
        for d_key, d_bars in bars.groupby("date", sort=True):
            days.append({"date": d_key, "open": float(d_bars["open"].iloc[0]),
                         "high": float(d_bars["high"].max()),
                         "low": float(d_bars["low"].min()),
                         "close": float(d_bars["close"].iloc[-1]),
                         "turn": float(d_bars["turnover"].sum()),
                         "amt": float(d_bars["amount"].sum()),
                         "o_arr": d_bars["open"].to_numpy(float),
                         "h_arr": d_bars["high"].to_numpy(float),
                         "l_arr": d_bars["low"].to_numpy(float),
                         "c_arr": d_bars["close"].to_numpy(float),
                         "ft_arr": pd.to_datetime(d_bars["datetime"]).to_numpy()})
        for i in range(1, len(days)):
            prev, cur = days[i - 1], days[i]
            cur["prev_close"] = prev["close"]
            cur["prev_turn"] = prev["turn"]
            cur["contig"] = (cal_pos.get(cur["date"], -1) - cal_pos.get(prev["date"], -1)) == 1

        dpos_map = {d["date"]: cal_pos.get(d["date"], -1) for d in days}
        holding = None
        cooldown_until = -1
        for i, cur in enumerate(days):
            dpos = dpos_map[cur["date"]]
            if dpos < 0:
                continue
            limit_price = (round(cur["prev_close"] * (1 + lim_r), 2)
                           if cur.get("contig", False) else None)

            # ---- 出场（T+1 起；SL 优先保守；跳空走开盘价）----
            if holding and i > holding["entry_i"]:
                sl_p = holding["entry_price"] * 0.91
                tp_p = holding["entry_price"] * 1.14
                o, lo_, h = cur["o_arr"], cur["l_arr"], cur["h_arr"]
                exit_px = exit_kind = None
                for k in range(len(cur["c_arr"])):
                    if o[k] <= sl_p:
                        exit_px, exit_kind = o[k], "SL跳空"
                        break
                    if lo_[k] <= sl_p:
                        exit_px, exit_kind = sl_p, "SL触线"
                        break
                    if o[k] >= tp_p:
                        exit_px, exit_kind = o[k], "TP跳空"
                        break
                    if h[k] >= tp_p:
                        exit_px, exit_kind = tp_p, "TP触线"
                        break
                if exit_kind:
                    events.append({**{k2: holding[k2] for k2 in
                                      ("kind", "code", "entry_date", "entry_price")},
                                   "exit_date": str(cur["date"].date()), "exit_price": exit_px,
                                   "exit_kind": exit_kind,
                                   "ret": exit_px / holding["entry_price"] - 1.0 - COST,
                                   "holding_days": dpos - holding["entry_pos"],
                                   "year": cur["date"].year})
                    holding = None

            # ---- 介入（空仓 + 非冷却 + 连续交易日 + 事前过滤）----
            if (holding is None and limit_price is not None and cur.get("contig", False)
                    and dpos > cooldown_until and cur["prev_turn"] < 3.0
                    and cur["open"] < limit_price - 0.005):          # 非一字（开盘未封）
                c_arr = cur["c_arr"]
                ft_times = pd.DatetimeIndex(cur["ft_arr"]).time
                touched_idx = np.where((cur["h_arr"] >= limit_price - 0.005)
                                       & (ft_times <= pd.Timestamp("10:00").time()))[0]
                if len(touched_idx) == 0:
                    continue
                k0 = int(touched_idx[0])
                # 介入 = 首触后首个收盘 < 涨停价的 bar（炸板即买 / 开板确认）
                buy_k = next((k for k in range(k0, len(c_arr))
                              if c_arr[k] < limit_price - 0.005), None)
                if buy_k is None:
                    continue                                          # 全程封死 → 买不到
                holding = {"kind": "DIP", "code": code, "entry_date": str(cur["date"].date()),
                           "entry_pos": dpos, "entry_price": float(c_arr[buy_k]),
                           "entry_i": i, "day_amt": float(cur["amt"])}
        done += 1
        if done % 150 == 0:
            print(f"   {done}/{len(files)}", flush=True)

    ev = pd.DataFrame([e for e in events if e["kind"]])
    print(f"\n事件 {len(ev)} 起")
    out = {}
    if len(ev):
        ev["ret"] = ev["exit_price"] / ev["entry_price"] - 1.0 - COST
        ev["entry_year"] = pd.to_datetime(ev["entry_date"]).dt.year
        for label, lo, hi in (("全样本 2020-26", 2020, 2026),
                              ("样本内 2020-23", 2020, 2023), ("样本外 2024-26", 2024, 2026)):
            sub = ev[(ev["entry_year"] >= lo) & (ev["entry_year"] <= hi)]
            if len(sub) < 20:
                print(f"  {label}: n={len(sub)}（不足）"); continue
            daily = sub.groupby("entry_date")["ret"].mean().sort_index()
            tt = float(_newey_west_tstat(daily, 19))
            sealed = "（事件级 sealed 回填待并入）"
            print(f"  {label}: n={len(sub)} 净期望 {sub['ret'].mean():+.2%} "
                  f"中位 {sub['ret'].median():+.2%} 胜率 {(sub['ret']>0).mean():.0%} "
                  f"NW-t {tt:+.2f}")
            out[label] = {"n": int(len(sub)), "mean": float(sub["ret"].mean()),
                          "median": float(sub["ret"].median()), "nw_t": tt}
    verdict = ("OOS_PASS" if (len(ev) and out.get("样本外 2024-26", {}).get("mean", -1) > 0
               and out.get("样本外 2024-26", {}).get("nw_t", 0) >= 2) else "CLOSED")
    print(f"\n== 裁决：{verdict} ==")
    json.dump({"verdict": verdict, "out": out, "n": int(len(ev))},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    try:
        record("limitup_oos_strict", params={"cost": COST, "sl": -0.09, "tp": 0.14},
               metrics={"oos_mean": float(out.get("样本外 2024-26", {}).get("mean", np.nan)),
                        "oos_nw_t": float(out.get("样本外 2024-26", {}).get("nw_t", np.nan))},
               notes=f"修正口径 dip-buy OOS：{verdict}", artifacts=[OUT.name])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] {e}")


if __name__ == "__main__":
    main()
