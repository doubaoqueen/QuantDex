"""第 49 步：条件持有曲线（D-094，用户 2026-11-19 改良提案"有潜力的标的可以不卖"）。

用户假设：表弟章法（追涨介入）的改良版——不是全部 T+1 无条件卖，而是
"达到预期就卖、有潜力的继续持有"。可测化（预注册，不扫参数）：
  介入 = 10:30 检查点（D-088 中最好的口径）
  潜力判据（事前可知）：T+1 收盘 > 介入价 → "潜力组"（继续持有）；
                        否则 → "弱势组"（T+1 卖出）
  持有期曲线：介入价 → T+1/T+3/T+5/T+10/T+20 收盘，净全成本，
              潜力组/弱势组/无差别全部分开呈现
  资本效率换算：持有 h 天的毛收益必须 ÷ h 才能与"1 天周转"的年化可比
              （持 5 天锁 5 天资金——这是"看起来更好"的最大幻觉源）
裁决：潜力组的长持有在资本效率换算后仍优于 T+1 卖 → 改良成立（NW-t ≥ 2）；
      否则 → 改良否决（T+1 全卖仍是最优规则）。
"""
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.factor_test import _newey_west_tstat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

COST_RT = 0.00025 * 2 + 0.0005 * 2 + 0.0005 + 0.00001 * 2
HORIZONS = (1, 3, 5, 10, 20)


def load_panel():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "g48", Path(__file__).resolve().parents[1] / "scripts" / "48_intraday_momentum_gate.py")
    g48 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g48)
    from src.data.storage import ParquetStore
    store = ParquetStore()
    agg = pd.read_parquet(g48.AGG_CACHE)
    panel = g48.prepare_panel(agg, store)
    return g48, panel, store


def add_forward_closes(panel: pd.DataFrame, horizons) -> pd.DataFrame:
    """逐标的追加大前向收盘（交易日序号差 = h 才有效，跨洞置 NaN）。"""
    p = panel.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = p.groupby("symbol")
    for h in horizons:
        nxt_close = g["day_close"].shift(-h)
        nxt_dpos = g["dpos"].shift(-h)
        p[f"close_f{h}"] = nxt_close.where((nxt_dpos - p["dpos"]) == h)
    return p


def entry_events(panel: pd.DataFrame) -> pd.DataFrame:
    """10:30 检查点介入事件 + T+1 分组（潜力/弱势）。"""
    ev = panel.dropna(subset=["entry_ckpt_1030", "exit_t1_close"]).copy()
    ev = ev[(ev["entry_ckpt_1030"] > 0.01) & (ev["exit_t1_close"] > 0.01)]   # 零价死行
    ev["t1_ret"] = ev["exit_t1_close"] / ev["entry_ckpt_1030"] - 1.0
    ev["group"] = np.where(ev["t1_ret"] > 0, "潜力组(T+1上涨)", "弱势组(T+1下跌)")
    return ev


def curve_report(ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for h in HORIZONS:
        col = f"close_f{h}"
        s_all = ev.dropna(subset=[col, "entry_ckpt_1030"]).copy()
        s_all = s_all[s_all[col] > 0.01]
        s_all["ret"] = s_all[col] / s_all["entry_ckpt_1030"] - 1.0 - COST_RT
        for grp, sub in (("无差别全部", s_all),
                         ("潜力组(T+1上涨)", s_all[s_all["group"].str.startswith("潜力")]),
                         ("弱势组(T+1下跌)", s_all[s_all["group"].str.startswith("弱势")])):
            if len(sub) < 100:
                continue
            daily = sub.groupby("date")["ret"].mean().sort_index()
            mean = float(sub["ret"].mean())
            rows.append({
                "horizon": h, "group": grp, "n": int(len(sub)),
                "mean_ret": mean, "median": float(sub["ret"].median()),
                "win_rate": float((sub["ret"] > 0).mean()),
                "mean_per_day": mean / h,                     # 资本效率换算：每占用一天的平均收益
                "daily_t": float(_newey_west_tstat(daily, 19)),
                "ann_equiv": float((1 + mean / h) ** 250 - 1) if mean / h > -1 else np.nan,
            })
    return pd.DataFrame(rows)


def main() -> None:
    g48, panel, store = load_panel()
    panel = add_forward_closes(panel, HORIZONS)
    ev = entry_events(panel)
    n_up = int((ev["group"].str.startswith("潜力")).sum())
    print(f">> 介入事件 {len(ev)} 起（10:30 检查点，{panel['date'].min().date()}~"
          f"{panel['date'].max().date()}）| T+1 上涨（潜力组）{n_up} / 下跌 {len(ev)-n_up}")

    rep = curve_report(ev)
    show = rep.copy()
    for c in ("mean_ret", "median", "win_rate", "mean_per_day", "ann_equiv"):
        show[c] = show[c].map(lambda v: f"{v:+.2%}" if pd.notna(v) else "n/a")
    show["daily_t"] = show["daily_t"].map(lambda v: f"{v:+.2f}")
    print("\n== 条件持有曲线（净全成本；ann_equiv=资本效率换算年化）==")
    print(show.to_string(index=False))

    # 公平裁决：潜力组"多持有到 h 天"的**增量收益** = close_f{h} / exit_t1_close - 1
    # （第 2~h 天的净贡献）——潜力组是事后按 T+1 结果切的组，直接对比无差别基线是循环论证。
    pot = ev[ev["group"].str.startswith("潜力")].dropna(subset=["exit_t1_close"])
    verdict = "rejected"
    inc_rows = []
    for h in (3, 5, 10, 20):
        col = f"close_f{h}"
        s = pot.dropna(subset=[col]).copy()
        s = s[s["exit_t1_close"] > 0.01]
        s["inc"] = s[col] / s["exit_t1_close"] - 1.0          # 第 2~h 天的净贡献（成本已计）
        daily = s.groupby("date")["inc"].mean().sort_index()
        mean_inc = float(s["inc"].mean())
        t_inc = float(_newey_west_tstat(daily, 19))
        per_day = mean_inc / h
        inc_rows.append({"horizon": h, "n": int(len(s)), "inc_mean": mean_inc,
                         "inc_median": float(s["inc"].median()),
                         "win": float((s["inc"] > 0).mean()), "nw_t": t_inc,
                         "per_day": per_day})
        print(f"   潜力组多持到 {h} 天的增量: {mean_inc:+.3%}（中位 "
              f"{float(s['inc'].median()):+.3%}，胜率 {(s['inc'] > 0).mean():.0%}，"
              f"NW-t {t_inc:+.2f}，每日期望 {per_day:+.4%}）", flush=True)
        if mean_inc > 0 and t_inc >= 2 and per_day > 0:
            verdict = f"passed(潜力组持有{h}天有正增量)"
    print("\n== 裁决：", verdict, "==")

    out = STORE_DIR / "research_results" / "hold_horizon.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"verdict": verdict, "report": rep.to_dict("records"),
                   "incremental": inc_rows, "n_events": int(len(ev))},
                  fh, ensure_ascii=False, indent=2, default=str)
    print(f"已保存 {out}")
    try:
        record("hold_horizon",
               params={"horizons": list(HORIZONS), "cost_rt": COST_RT},
               metrics={f"{r['group'][:4]}/h{r['horizon']}": float(r["mean_per_day"])
                        for _, r in rep.iterrows()},
               notes=f"条件持有曲线裁决：{verdict}",
               artifacts=["research_results/hold_horizon.json"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
