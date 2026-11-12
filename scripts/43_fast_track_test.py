"""第 43 步：快车道检验——高分新面孔的"提前录取"是否有效（D-079，用户 2026-11-09 构想）。

用户观察：每日漂移量大（重合度 3 周 100%→34%）但未归类；持续挤进高分区的
新标的"有资格提前录取"——不用等 19 天。自省：不贪快，是"行情很快过去"的
灵活性 + 稳妥底线。

理论锚点（既有证据）：rev_5 预测力仅 1-5 天（D-031：1日 NW-t -3.58），而
调仓节奏 20 日——**持有期错配自 D-003 记录在案**。D-074 测出去掉 rev_5
+23pp（20 日节奏下它是拖累）；但另一解法未测：**给新鲜高分开 5-10 日快车道**，
让 5 日 alpha 在窗口内兑现。用户的构想恰好指向这个缺口。

两个检验（全向量化——本机 14600KF 在 pandas 原生逐行操作中偶发硬崩，
11-08/11-09 三次实测，故全部用矩阵化运算消除逐事件 .loc）：
  E1 事件研究：池内标的**首次**进入全池综合分 Top-15（此前 ≥5 日在榜外）→
     后续 1/5/10/20 日超额 vs 当日池中位；分"持续进入"（≥3 日连留）与
     "一日闪现"对比——检验漂移信号的持续性与衰减。
  E2 策略 A/B：TopkDropout 快车道（每 5/10 日最多换 3 只 = 提前录取机制，
     qlib 同款已实现）vs 基线严格 Top50/20日；3 因子与 2 因子（去 rev_5）
     双口径。保真成本、开盘执行、OOS(2022+)。
纪律：分年方向一致才可采纳（D-064/D-075 同款）；4-7 日噪声不定罪。
用法：python scripts/43_fast_track_test.py
"""
import faulthandler
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
SCORE_CACHE = OUT_DIR / "fast_track_score_cache.parquet"
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


def main() -> None:
    faulthandler.enable(file=sys.stderr)  # 段错误也打印堆栈（本机原生崩溃诊断）
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)

    if SCORE_CACHE.exists():
        panel = pd.read_parquet(SCORE_CACHE)
        panel["date"] = pd.DatetimeIndex(panel["date"])
        panel["symbol"] = panel["symbol"].astype(str)
        print(f">> 面板缓存命中：{len(panel)} 行", flush=True)
    else:
        print(f">> 构建面板（{len(symbols)} 只，exclude_st=True）...", flush=True)
        panel = build_factor_panel(store, symbols, start="20180101",
                                   pit_constituents=pit, exclude_st=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(SCORE_CACHE)
        print(">> 面板已缓存", flush=True)
    panel["date"] = pd.DatetimeIndex(panel["date"]).astype("datetime64[ns]")
    panel["symbol"] = panel["symbol"].astype(str)
    score = build_score(panel)
    panel_dates = pd.DatetimeIndex(sorted(score.index))
    prices, opens = load_price_matrix(symbols, start="20180101", store=store)
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    b = get_kline("000300", adjust="none", store=store).set_index("date")["close"]
    bench_ret = b.pct_change()
    px = prices.ffill().reindex(columns=score.columns)  # 对齐到面板列（D-079 修复列数不匹配）
    pool_ret_med = {n: (px.shift(-n) / px - 1.0).median(axis=1) for n in FWD}

    # ---- E1 事件研究（全向量化）----
    print(f">> E1 事件研究：Top{TOP_K} 新面孔（榜外 ≥{MIN_OUT_DAYS} 日后首次进入）",
          flush=True)
    in_top = score.rank(ascending=False, axis=1) <= TOP_K
    out5 = (~in_top).rolling(MIN_OUT_DAYS, min_periods=MIN_OUT_DAYS).sum().shift(1)
    fresh = in_top & (out5 == MIN_OUT_DAYS)
    # 前向连续在榜天数（反向累计，向量化）
    arr = in_top.to_numpy()
    streak = np.zeros_like(arr, dtype=np.int64)
    for i in range(len(arr) - 2, -1, -1):
        streak[i] = np.where(arr[i], streak[i + 1] + 1, 0)
    streak_df = pd.DataFrame(streak, index=in_top.index, columns=in_top.columns)

    parts = []
    for n in FWD:
        marr = fresh.to_numpy()
        fut = px.shift(-n).to_numpy()
        cur = px.to_numpy()
        stk = streak_df.to_numpy()
        dts = px.index.to_numpy()
        syms = px.columns.to_numpy()
        med_arr = pool_ret_med[n].reindex(px.index).to_numpy(float)
        recs = []
        for i in range(len(dts)):
            for j in range(len(syms)):
                if marr[i, j] and np.isfinite(fut[i, j]) and np.isfinite(cur[i, j])                         and cur[i, j] > 0:
                    f = fut[i, j] / cur[i, j] - 1.0
                    recs.append((dts[i], syms[j], f, f - med_arr[i], int(stk[i, j])))
        part = pd.DataFrame(recs, columns=["date", "symbol", "fwd", "exc", "persist"])
        part["horizon"] = n
        parts.append(part)
        print(f"   窗口 {n} 日：{len(part)} 条", flush=True)
    evdf = pd.concat(parts, ignore_index=True)
    evdf["year"] = evdf["date"].dt.year
    evdf["persistent"] = evdf["persist"] >= 3
    print(f"   新面孔事件（事件×窗口）{len(evdf)} 条", flush=True)

    print("\n== E1 新面孔进入 Top15 后超额（vs 当日池中位）==")
    for n in FWD:
        sub = evdf[evdf["horizon"] == n]
        by_day = sub.groupby("date")["exc"].mean().dropna()
        nw = _newey_west_tstat(by_day, 19) if len(by_day) > 30 else np.nan
        pers = sub[sub["persistent"]]
        flash = sub[~sub["persistent"]]
        print(f"  fwd{n:<3} 全体 均值 {sub['exc'].mean():+.3%} 中位 "
              f"{sub['exc'].median():+.3%} | 跑赢池比例 "
              f"{(sub['exc'] > 0).mean():.0%} | NW-t {nw:+.2f}")
        print(f"        持续进入(n={len(pers)}) {pers['exc'].mean():+.3%} | "
              f"一日闪现(n={len(flash)}) {flash['exc'].mean():+.3%}")
    y5m = evdf[evdf["horizon"] == 5].groupby("year")["exc"].median()
    print("  分年 fwd5 超额中位：" + " | ".join(f"{y}: {v:+.2%}" for y, v in y5m.items()))

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
        nav = res["nav"]
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
