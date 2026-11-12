"""第 43 步：快车道检验——高分新面孔的"提前录取"是否有效（D-079，用户 2026-11-09 构想）。

用户观察：每日漂移量大（重合度 3 周 100%→34%）但未归类；持续挤进高分区的
新标的"有资格提前录取"——不用等 19 天。自省：不贪快，是"行情很快过去"的
灵活性 + 稳妥底线。

理论锚点（既有证据）：rev_5 预测力仅 1-5 天（D-031：1日 NW-t -3.58），而
调仓节奏 20 日——**持有期错配自 D-003 记录在案**。D-074 测出去掉 rev_5
+23pp（20 日节奏下它是拖累）；但另一解法未测：**给新鲜高分开 5-10 日快车道**，
让 5 日 alpha 在窗口内兑现。用户的构想恰好指向这个缺口。

两个检验：
  E1 事件研究：池内标的**首次**进入全池综合分 Top-15（此前 ≥5 日在榜外）→
     后续 1/5/10/20 日超额 vs 当日池中位；分"持续进入"（≥3 日连留）与
     "一日闪现"对比——检验漂移信号的持续性与衰减。
  E2 策略 A/B：TopkDropout 快车道（每 5/10 日最多换 3 只 = 提前录取机制，
     qlib 同款已实现）vs 基线严格 Top50/20日；3 因子与 2 因子（去 rev_5）
     双口径。保真成本、开盘执行、OOS(2022+)。
纪律：分年方向一致才可采纳（D-064/D-075 同款）；4-7 日噪声不定罪。
用法：python scripts/43_fast_track_test.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import build_factor_panel
from src.analysis.experiments import record
from src.backtest.portfolio import run_portfolio_backtest
from src.data import universe
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import (
    STRATEGY_FACTORS, composite_topn, topk_dropout_topn,
)

OUT_DIR = STORE_DIR / "research_results"
TOP_K = 15          # 候选前 15 = 高分区定义（与 live 同口径）
MIN_OUT_DAYS = 5    # "新面孔"要求此前连续在榜外 ≥5 日
FWD = (1, 5, 10, 20)


def build_score(panel: pd.DataFrame) -> pd.DataFrame:
    """综合分宽表（与流水线同口径：三因子方向调整 rank 等权）。"""
    r_t = panel.pivot(index="date", columns="symbol",
                      values="turnover_20").rank(axis=1)
    r_v = panel.pivot(index="date", columns="symbol",
                      values="vol_20").rank(axis=1)
    r_r = panel.pivot(index="date", columns="symbol",
                      values="rev_5").rank(axis=1)
    score = ((1 - r_t) + (1 - r_v) + (1 - r_r)) / 3
    return score.sort_index()


def fresh_entries(score: pd.DataFrame, top_k: int = TOP_K,
                  min_out: int = MIN_OUT_DAYS) -> pd.DataFrame:
    """新面孔事件：榜外 ≥min_out 日后首次进入 Top-K，附连续在榜天数 persist。"""
    in_top = score.rank(ascending=False, axis=1) <= top_k
    dates = score.index
    events = []
    for sym in in_top.columns:
        seq = in_top[sym].to_numpy()
        out_run = 0
        for i, v in enumerate(seq):
            if not v:
                out_run += 1
                continue
            if out_run >= min_out:
                n = 1
                k = i + 1
                while k < len(seq) and seq[k]:
                    n += 1
                    k += 1
                events.append({"symbol": sym, "date": dates[i], "persist": n})
            out_run = 0
    return pd.DataFrame(events, columns=["symbol", "date", "persist"])


def main() -> None:
    faulthandler_ok = True
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 构建面板（{len(symbols)} 只，exclude_st=True）...", flush=True)
    panel = build_factor_panel(store, symbols, start="20180101",
                               pit_constituents=pit, exclude_st=True)
    panel_dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel["date"]).unique()))
    prices, opens = load_price_matrix(symbols, start="20180101", store=store)
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    b = get_kline("000300", adjust="none", store=store).set_index("date")["close"]
    bench_ret = b.pct_change()
    px = prices.ffill()
    pool_med = px.median(axis=1)

    score = build_score(panel)

    # ---- E1 事件研究 ----
    print(f">> E1 事件研究：Top{TOP_K} 新面孔（榜外 ≥{MIN_OUT_DAYS} 日后首次进入）",
          flush=True)
    ev = fresh_entries(score, TOP_K, MIN_OUT_DAYS)
    ev["date"] = pd.to_datetime(ev["date"])
    ev["year"] = ev["date"].dt.year
    rows = []
    for r in ev.itertuples(index=False):
        t = pd.Timestamp(r.date)
        if t not in px.index:
            continue
        row = {"date": t, "year": t.year, "symbol": r.symbol,
               "persist": int(r.persist)}
        for n in FWD:
            if r.symbol not in px.columns:
                continue
            f = px.shift(-n).loc[t, r.symbol] / px.loc[t, r.symbol] - 1.0
            m = pool_med.loc[t]
            if pd.notna(f) and pd.notna(m):
                row[f"fwd{n}"] = float(f - m)
        rows.append(row)
    evdf = pd.DataFrame(rows)
    evdf["persistent"] = evdf["persist"] >= 3
    print(f"   新面孔事件 {len(evdf)} 起（持续 ≥3 日 {int(evdf['persistent'].sum())}）",
          flush=True)

    print("\n== E1 新面孔进入 Top15 后超额（vs 当日池中位）==")
    for n in FWD:
        sub = evdf.dropna(subset=[f"fwd{n}"])
        if sub.empty:
            continue
        by_day = sub.groupby("date")[f"fwd{n}"].mean().dropna()
        nw = _newey_west_tstat(by_day, 19) if len(by_day) > 30 else np.nan
        pers = sub[sub["persistent"]]
        flash = sub[~sub["persistent"]]
        print(f"  fwd{n:<3} 全体 均值 {sub[f'fwd{n}'].mean():+.3%} 中位 "
              f"{sub[f'fwd{n}'].median():+.3%} | 跑赢池比例 "
              f"{(sub[f'fwd{n}'] > 0).mean():.0%} | NW-t {nw:+.2f}")
        print(f"        持续进入(n={len(pers)}) {pers[f'fwd{n}'].mean():+.3%} | "
              f"一日闪现(n={len(flash)}) {flash[f'fwd{n}'].mean():+.3%}")
    y5m = evdf.dropna(subset=["fwd5"]).groupby("year")["fwd5"].median()
    print("  分年 fwd5 中位：" + " | ".join(f"{y}: {v:+.2%}" for y, v in y5m.items()))

    # ---- E2 策略 A/B ----
    print("\n>> E2 策略回测（快车道 vs 基线）...", flush=True)
    arms = {
        "A基线 strict/20d": composite_topn(panel, STRATEGY_FACTORS["corrected"],
                                           top_n=50, rebalance_days=20),
        "B快车道 3因子 5日/换3": topk_dropout_topn(
            panel, STRATEGY_FACTORS["corrected"], topk=50, n_drop=3,
            rebalance_days=5),
        "C快车道 3因子 10日/换3": topk_dropout_topn(
            panel, STRATEGY_FACTORS["corrected"], topk=50, n_drop=3,
            rebalance_days=10),
        "D快车道 2因子 5日/换3": topk_dropout_topn(
            panel, STRATEGY_FACTORS["corrected_2f"], topk=50, n_drop=3,
            rebalance_days=5),
        "E对照 2因子 strict/20d": composite_topn(
            panel, STRATEGY_FACTORS["corrected_2f"], top_n=50, rebalance_days=20),
    }
    rows = []
    for tag, W in arms.items():
        res = run_portfolio_backtest(
            W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
            stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
            init_capital=1_000_000.0, benchmark_returns=bench_ret,
            open_prices=opens)
        nav, ret = res["nav"], res["daily_ret"]
        ann = nav.iloc[-1] ** (250 / max(len(nav), 1)) - 1
        mdd = float((nav / nav.cummax() - 1).min())
        row = {"arm": tag, "total": float(nav.iloc[-1] - 1), "ann": float(ann),
               "mdd": mdd, "calmar": ann / abs(mdd) if mdd < 0 else np.nan,
               "turnover": float(res["turnover"].sum() / max(len(nav), 1) * 250)}
        oos = nav.index >= pd.Timestamp("2022-01-01")
        if oos.any():
            n2 = nav[oos]
            row["oos_total"] = float(n2.iloc[-1] / n2.iloc[0] - 1)
            oos_days = max(len(n2), 1)
            row["oos_ann"] = float((n2.iloc[-1] / n2.iloc[0]) ** (250 / oos_days) - 1)
            dd2 = float((n2 / n2.cummax() - 1).min())
            row["oos_calmar"] = row["oos_ann"] / abs(dd2) if dd2 < 0 else np.nan
        rows.append(row)
        print(f"   [done] {tag}", flush=True)

    res = pd.DataFrame(rows)
    print("\n== E2 快车道 vs 基线（保真口径，开盘执行，ST 剔除）==")
    print(f"{'方案':<26} {'全期':>9} {'年化':>8} {'MDD':>8} {'Calmar':>7} {'换手':>6} "
          f"| {'OOS':>9} {'OOS年化':>8} {'OOS_Calmar':>7}")
    for _, r in res.iterrows():
        print(f"{r['arm']:<26} {r['total']:>+9.1%} {r['ann']:>+8.1%} {r['mdd']:>8.1%} "
              f"{r['calmar']:>7.2f} {r['turnover']:>5.0%} "
              f"| {r['oos_total']:>+9.1%} {r['oos_ann']:>+8.1%} {r['oos_calmar']:>7.2f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    evdf.to_parquet(OUT_DIR / "fast_track_events.parquet", index=False)
    res.to_parquet(OUT_DIR / "fast_track_arms.parquet", index=False)
    try:
        best = res.loc[res["oos_calmar"].idxmax()]
        record("fast_track_test",
               params={"arms": list(res["arm"]), "top_k": TOP_K,
                       "min_out": MIN_OUT_DAYS},
               metrics={f"{r['arm']}|oos_calmar": float(r["oos_calmar"])
                        for _, r in res.iterrows()},
               notes="快车道检验：事件研究+策略A/B，结论见 DECISIONS D-079",
               artifacts=["research_results/fast_track_arms.parquet",
                          "research_results/fast_track_events.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
