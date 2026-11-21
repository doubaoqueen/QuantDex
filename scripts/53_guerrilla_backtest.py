"""第 53 步：表弟游击首触回测（D-100，用户双阈值策略的完整检验，2026-11-20 对齐版）。

策略（用户定义，参数冻结——冷却三档为用户点名要的敏感性对比）：
  入场：M1 追涨介入（日内涨幅首越 3% 且距板 ≥2pp）/ M2 近板介入（首越 7.8%/17.8%
        且未封死）——与监控器 D-089 同定义；介入价 = 触发 bar 收盘。
  出场：第一触达（5 分钟 bar 判序，SL 优先保守）——止损 -9%（内移避开主板跌停
        冻结）/ 止盈 +14%；跳空开盘穿越 → 出场价 = 开盘价（承受跳空滑点）。
  T+1 规则：介入当日不可出场，出场扫描从次一交易日起；持有跨停牌延续。
  冷却：止损出场后 N 个交易日禁同一标的再入（N ∈ {0,3,5} 三档；止盈出场不冷却）；
        同日再入场禁止（预注册简化）。
  对照基线：同入场、T+1 开盘无条件卖（D-088 口径）——双阈值的增量由此度量。

口径：PIT + ST 剔除；全成本 22.7bp/回合；数据 = 分钟资产 bar 级扫描。
"""
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import st_symbol_set
from src.data.storage import ParquetStore
from src.logsetup import get_logger

COST_RT = 0.00025 * 2 + 0.0005 * 2 + 0.0005 + 0.00001 * 2
SL, TP = 0.91, 1.14                       # -9% / +14%
COOLDOWNS = (0, 3, 5)
EPS = 0.005
OUT = STORE_DIR / "research_results" / "guerrilla_first_touch.json"


def _limit_ratio(code: str) -> float:
    return 0.195 if code.startswith(("30", "68")) else 0.095


def simulate_symbol(code: str, bars: pd.DataFrame, cal_pos: dict,
                    cooldown_days: int) -> list[dict]:
    """逐日状态机：① 出场扫描（T+1 起，SL 优先）② 介入检测（空仓+非冷却）。"""
    lim_r = _limit_ratio(code)
    events = []
    holding = None            # dict(kind, code, entry_date, entry_pos, entry_price, t1_open)
    cooldown_until = -1
    prev_close = None
    prev_pos = None
    for day_key, day_bars in bars.groupby("date", sort=True):
        dpos = cal_pos.get(day_key)
        if dpos is None:
            prev_close = float(day_bars["close"].iloc[-1])
            prev_pos = None
            continue
        o = day_bars["open"].to_numpy(float)
        h = day_bars["high"].to_numpy(float)
        low = day_bars["low"].to_numpy(float)
        c = day_bars["close"].to_numpy(float)
        contiguous = prev_pos is not None and (dpos - prev_pos) == 1

        # ---- 1) 出场扫描（隔日 T+1 起；跳空走开盘价；SL 优先保守）----
        if holding and dpos > holding["entry_pos"]:
            if holding.get("t1_open") is None and len(o):
                holding["t1_open"] = float(o[0])          # 首个次日的开盘（基线用）
            sl_p = holding["entry_price"] * SL
            tp_p = holding["entry_price"] * TP
            exit_px = exit_kind = None
            for k in range(len(c)):
                if o[k] <= sl_p:
                    exit_px, exit_kind = o[k], "SL跳空"
                    break
                if low[k] <= sl_p:
                    exit_px, exit_kind = sl_p, "SL触线"
                    break
                if o[k] >= tp_p:
                    exit_px, exit_kind = o[k], "TP跳空"
                    break
                if h[k] >= tp_p:
                    exit_px, exit_kind = tp_p, "TP触线"
                    break
            if exit_kind is not None:
                events.append({**holding,
                               "exit_date": str(day_key.date()),
                               "exit_price": exit_px, "exit_kind": exit_kind,
                               "ret": exit_px / holding["entry_price"] - 1.0 - COST_RT,
                               "holding_days": dpos - holding["entry_pos"],
                               "baseline_ret": ((holding.get("t1_open") or np.nan)
                                                / holding["entry_price"] - 1.0 - COST_RT)})
                if exit_kind.startswith("SL") and cooldown_days > 0:
                    cooldown_until = dpos + cooldown_days
                holding = None

        # ---- 2) 介入检测（空仓 + 非冷却 + 昨收连续有效）----
        if (not holding and prev_close is not None and contiguous
                and dpos > cooldown_until):
            limit_price = round(prev_close * (1 + lim_r), 2)
            gain = c / prev_close - 1.0
            unsealed = c < limit_price - EPS
            nb = 0.178 if lim_r > 0.1 else 0.078
            m1 = np.where((gain >= 0.03) & (gain < nb) & unsealed)[0]
            m2 = np.where((gain >= nb) & unsealed)[0]
            kind, k_idx = None, None
            if len(m1) and len(m2):
                kind, k_idx = ("M1", int(m1[0])) if m1[0] <= m2[0] else ("M2", int(m2[0]))
            elif len(m1):
                kind, k_idx = "M1", int(m1[0])
            elif len(m2):
                kind, k_idx = "M2", int(m2[0])
            if kind is not None:
                holding = {"kind": kind, "code": code, "entry_date": str(day_key.date()),
                           "entry_pos": dpos, "entry_price": float(c[k_idx]),
                           "entry_gain": float(gain[k_idx]), "t1_open": None}

        prev_close = float(c[-1])
        prev_pos = dpos
    return events


def main() -> None:
    log = get_logger(__file__)
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
    log.info(">> 游击首触回测：%d 只（PIT+ST 剔除），冷却三档 %s", len(files), COOLDOWNS)

    all_events = {cd: [] for cd in COOLDOWNS}
    done = 0
    for f in files:
        code = f.name.split("__")[0]
        bars = pd.read_parquet(f, columns=["datetime", "open", "high", "low", "close"])
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
        for cd in COOLDOWNS:
            all_events[cd].extend(simulate_symbol(code, bars, cal_pos, cd))
        done += 1
        if done % 150 == 0:
            log.info("   %d/%d", done, len(files))

    idx = s.read("daily", "000300__none", columns=["date", "close"])
    bench_ret = (idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"]
                 .pct_change())
    out = {}
    for cd in COOLDOWNS:
        ev = pd.DataFrame(all_events[cd])
        if ev.empty:
            log.info("[冷却 %d] 无事件", cd)
            continue
        ev["ret"] = ev["exit_price"] / ev["entry_price"] - 1.0 - COST_RT
        ev["baseline_ret"] = pd.to_numeric(ev.get("baseline_ret"), errors="coerce")
        ev["entry_d"] = pd.to_datetime(ev["entry_date"])
        rows = []
        for kind in ("M1", "M2", "全部"):
            sub = ev if kind == "全部" else ev[ev["kind"] == kind]
            if len(sub) < 30:
                continue
            daily = sub.groupby("entry_d")["ret"].mean().sort_index()
            base = sub.dropna(subset=["baseline_ret"])
            base_mean = float(base["baseline_ret"].mean()) if len(base) else np.nan
            rows.append({"kind": kind, "n": int(len(sub)),
                         "tp_first": float((sub["exit_kind"].str.startswith("TP")).mean()),
                         "sl_first": float((sub["exit_kind"].str.startswith("SL")).mean()),
                         "gap_exit": float(sub["exit_kind"].str.endswith("跳空").mean()),
                         "mean": float(sub["ret"].mean()),
                         "median": float(sub["ret"].median()),
                         "win": float((sub["ret"] > 0).mean()),
                         "hold": float(sub["holding_days"].mean()),
                         "daily_t": float(_newey_west_tstat(daily, 19)),
                         "baseline_mean": base_mean})
        out[f"cd{cd}"] = {"rows": rows, "n_events": int(len(ev))}
        log.info("[冷却 %d 天]", cd)
        for r in rows:
            bl = f"{r['baseline_mean']:+.2%}" if pd.notna(r["baseline_mean"]) else "n/a"
            log.info("   %-4s n=%-6d 止盈先触 %.1f%% | 止损先触 %.1f%% | 跳空出场 %.1f%% "
                     "| 净均值 %+.2f%% 中位 %+.2f%% 胜率 %.0f%% "
                     "| 持有 %.1f 日 | NW-t %+.2f | 基线(无条件T+1卖) %s",
                     r['kind'], r['n'], r['tp_first'] * 100, r['sl_first'] * 100,
                     r['gap_exit'] * 100, r['mean'] * 100, r['median'] * 100,
                     r['win'] * 100, r['hold'], r['daily_t'], bl)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    log.info("已存 %s", OUT)


if __name__ == "__main__":
    main()
