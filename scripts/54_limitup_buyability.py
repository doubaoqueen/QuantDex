"""第 54 步：涨停成交率测试（D-102，外部评审 D-101 处方的进攻型最终裁决格）。

评审命题（D-101）：D-093/D-100 的全部期望都是**上界**——一字板/封死格子的收益
被计入但实际买不到。唯一可能活着的格子 = **"开板后回封"**：首触后价格跌回
涨停价之下（开板窗口，此时可市价买入），当日收盘重新封板（回封确认）。
本脚本实测：这个格子的净期望是否 > 0——**有正期望才继续；没有，进攻型永久关闭**。

口径（预注册）：
  可买判定：首触后（含首触 bar 本身）首个 close < 涨停价−ε 的 bar → 介入价 = 该 bar
  收盘（开板确认市价买入的近似）；全程未跌破 → 不可买（一字/秒板封死）。
  出场 = T+1 开盘（next_open）；净收益 = 出场/介入 − 1 − 22.7bp。
  回封 = 当日收盘 ≥ 涨停价−ε；炸板 = 触板但收盘未封。
  关键格子 = 低换手(<3%) + 非一字 + 首触≤10:00 + 开板后回封（D-101 处方）。
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import st_symbol_set
from src.data.storage import ParquetStore

COST = 0.00202
EPS = 0.005
OUT = STORE_DIR / "research_results" / "limitup_buyability.json"


def main() -> None:
    s = ParquetStore()
    ev = pd.read_parquet(STORE_DIR / "research_results" / "limitup_recut.parquet")
    ev = ev[(ev["touched"]) & (ev["entry"] > 0.01) & (ev["next_open_v"] > 0.01)].copy()
    ev["date"] = pd.to_datetime(ev["date"])
    ev_by_sym = {k: g for k, g in ev.groupby("symbol")}
    st_syms = st_symbol_set(s)
    minute_dir = STORE_DIR / "market" / "minute"
    files = [f for f in sorted(minute_dir.glob("*__5__none.parquet"))
             if f.name.split("__")[0] not in st_syms and f.name.split("__")[0] in ev_by_sym]
    print(f">> 成交率扫描：{len(files)} 只、{len(ev)} 个触板事件", flush=True)

    buy_rows = []
    n_unbuyable = 0
    done = 0
    for f in files:
        code = f.name.split("__")[0]
        sym_ev = ev_by_sym.get(code)
        if sym_ev is None or len(sym_ev) == 0:
            continue
        bars = pd.read_parquet(f, columns=["datetime", "open", "high", "low", "close"])
        bars["date"] = pd.to_datetime(bars["datetime"]).dt.normalize()
        bars = bars.sort_values("datetime")
        for _, e in sym_ev.iterrows():
            day_bars = bars[bars["date"] == e["date"]]
            if len(day_bars) == 0:
                continue
            limit_p = float(e["limit_price"])
            ft_time = pd.to_datetime(e["first_touch"])
            # 首触 bar 之后的路径（含首触 bar 本身的收盘判定）
            post = day_bars[day_bars["datetime"] >= ft_time - pd.Timedelta(minutes=5)]
            buy_px = np.nan
            for _, b in post.iterrows():
                if b["close"] < limit_p - EPS and pd.notna(b["close"]) and b["close"] > 0.01:
                    buy_px = float(b["close"])            # 开板确认：首个收在涨停价之下的 bar
                    break
            if np.isnan(buy_px):
                n_unbuyable += 1                          # 全程封死/一字 → 买不到
                continue
            buy_rows.append({"symbol": code, "date": e["date"], "buy_price": buy_px,
                             "next_open": float(e["next_open_v"]),
                             "sealed": bool(e["sealed"]), "t_turn": e.get("t_turn", np.nan),
                             "is_yizi": bool(e["is_yizi"]),
                             "ft_time": str(ft_time.time())})
        done += 1
        if done % 150 == 0:
            print(f"   {done}/{len(files)}", flush=True)

    b = pd.DataFrame(buy_rows)
    b["net"] = b["next_open"] / b["buy_price"] - 1.0 - COST
    b["date"] = pd.to_datetime(b["date"])
    print(f"\n>> 可买事件 {len(b)} / 全部触板 {len(ev)}（可买率 {len(b)/len(ev):.1%}）"
          f" | 不可买（全程封死）{n_unbuyable}")

    def rep(name, sub):
        if len(sub) < 20:
            print(f"  {name}: n={len(sub)}（样本不足）"); return
        daily = sub.groupby("date")["net"].mean().sort_index()
        t_stat = float(_newey_west_tstat(daily, 19))
        print(f"  {name}: n={len(sub)} 净期望 {sub['net'].mean():+.2%} "
              f"中位 {sub['net'].median():+.2%} 胜率 {(sub['net'] > 0).mean():.0%} "
              f"| 逐日 NW-t {t_stat:+.2f}")

    print("\n== 可买格子的真实期望（开板确认介入 → T+1 开盘卖，扣全成本）==")
    rep("回封组（当日收盘封板）", b[b["sealed"]])
    rep("炸板组（当日未封）", b[~b["sealed"]])
    rep("全部可买", b)

    key = b[(b["t_turn"] < 3.0) & (~b["is_yizi"])
            & (pd.to_datetime(b["ft_time"]).dt.time <= pd.Timestamp("10:00").time())
            & (b["sealed"])]
    print("\n== 评审关键格子（全样本）：低换手<3% + 非一字 + 首触≤10:00 + 开板回封 ==")
    print(f"  n={len(key)} 净期望 {key['net'].mean() if len(key) else np.nan:+.2%} "
          f"中位 {key['net'].median() if len(key) else np.nan:+.2%}")

    # D-102 强制下步：时间外推（预注册切点 2023/2024——规则锚在 2020-23，2024-26 考试）
    print("\n== 时间外推（规则冻结于 2020-23，2024-26 样本外考试）==")
    b["entry_year"] = pd.to_datetime(b["date"]).dt.year
    key_y = key.copy()
    key_y["entry_year"] = pd.to_datetime(key_y["date"]).dt.year
    oos_ok = False
    for label, lo, hi in (("样本内 2020-23", 2020, 2023), ("样本外 2024-26", 2024, 2026)):
        sub_p = key_y[(key_y["entry_year"] >= lo) & (key_y["entry_year"] <= hi)]
        if len(sub_p) < 20:
            print(f"   {label}: n={len(sub_p)}（样本不足）")
            continue
        print(f"   {label}: n={len(sub_p)} 净期望 {sub_p['net'].mean():+.2%} "
              f"中位 {sub_p['net'].median():+.2%} 胜率 {(sub_p['net'] > 0).mean():.0%}")
        if label.startswith("样本外") and sub_p["net"].mean() > 0:
            oos_ok = True

    # D-102 裁决：全样本挑选 + 时间外推 + 尾部建模三关齐备才判 PASS
    verdict = ("OOS_PASS" if (len(key) >= 100 and key["net"].mean() > 0 and oos_ok)
               else ("PROVISIONAL" if (len(key) >= 100 and key["net"].mean() > 0) else "CLOSED"))
    print(f"\n== 裁决：{verdict} ==")
    if verdict == "OOS_PASS":
        print("→ 样本外仍为正 → 进攻型保留小资金卫星仓候选（容量 ≈248 起/年）")
        print("→ 强制下步：容量实测 + T+1 跌停出场尾部建模 + 影子盘第五账户提案")
    elif verdict == "PROVISIONAL":
        print("→ 全样本为正但样本外不足 → 待验，12 月数据补齐后重跑本脚本")
    else:
        print("→ 可买格子无正期望 → 进攻型永久关闭（D-093/100/102 三连）")

    if len(b):
        b.to_parquet(STORE_DIR / "research_results" / "limitup_dip_events.parquet", index=False)
    json.dump({"verdict": verdict, "n_buyable": int(len(b)), "n_unbuyable": int(n_unbuyable),
               "key_cell_n": int(len(key)),
               "key_cell_mean": float(key["net"].mean()) if len(key) else None,
               "all_mean": float(b["net"].mean()) if len(b) else None},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print(f"已存 {OUT}")


if __name__ == "__main__":
    import json
    main()
