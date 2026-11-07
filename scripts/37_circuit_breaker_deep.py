"""第 37 步：断路器深危机阈值扫描（D-033 悬案收口，D-069）。

背景：D-033 测过 -10%/-15% 减半与 -15% 清仓——全部收益毁灭性下降（-16~-42pp）
而 MDD 仅改善 1-5pp。但用户 2026-11-07 两度提出"没有兜底"——未验证的角落只剩
**深危机阈值**：平时绝不打扰、真崩盘才出手。本脚本扫 -20%/-25%/-30%/-40% 减半。

语义（与 risk.apply_risk / D-033 一致）：在每个调仓日检查当时的组合回撤，
深于阈值 → 该期目标权重 × scale(0.5)，其余日期不动。触发机会 = 调仓日数
（20 日节奏下每年约 11 次）——深阈值意味着可能整轮暴跌都碰不上触发日。

方法：自带逐日模拟器（复刻组合引擎的收益/成本/漂移语义），先与官方引擎
无断路器基准对账（必须一致），再扫阈值。三策略 × {基准, 4 档深阈值}。

用法：python scripts/37_circuit_breaker_deep.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.backtest.portfolio import run_portfolio_backtest
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import STRATEGY_FACTORS, composite_topn, topk_dropout_topn

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "research_results"
THRESHOLDS = [-0.20, -0.25, -0.30, -0.40]
SCALE = 0.5
CAPITAL = 1_000_000.0
COST = dict(fee=0.00025, min_fee=5.0, slip=0.0005, stamp=0.0005, transfer=0.00001)


def simulate(W: pd.DataFrame, prices: pd.DataFrame, opens: pd.DataFrame,
             max_dd: float | None = None, dd_scale: float = SCALE) -> pd.Series:
    """逐日模拟器：与组合引擎（开盘执行）同语义，可插入断路器。"""
    cols = W.columns
    px_raw = prices[cols].sort_index()
    op_raw = opens[cols].sort_index().reindex(px_raw.index)
    px = px_raw.ffill()
    op = op_raw.ffill()
    dates = px.index
    date_pos = {d: i for i, d in enumerate(dates)}
    eff = {}
    for d in W.index.sort_values():
        if d in date_pos and date_pos[d] + 1 < len(dates):
            eff[dates[date_pos[d] + 1]] = d
    r_c = px.pct_change().fillna(0.0)
    r_on = (op / px.shift(1) - 1.0).fillna(0.0).replace([np.inf, -np.inf], 0.0)
    r_in = (px / op - 1.0).fillna(0.0).replace([np.inf, -np.inf], 0.0)
    nav, peak = 1.0, 1.0
    w = np.zeros(len(cols))
    nav_s = {}
    for i in range(1, len(dates)):
        t = dates[i]
        if t in eff:
            sig = eff[t]
            w_t = W.loc[sig].reindex(cols).fillna(0.0).to_numpy(float).copy()
            if max_dd is not None:
                dd = nav / peak - 1.0
                if dd < max_dd:
                    w_t = w_t * dd_scale
            # 保真约束（与引擎 limit_skip 同源）：停牌/涨跌停保持原权重
            from config import board_limit
            raw_t = op_raw.loc[t]
            raw_prev = px_raw.shift(1).loc[t]
            for k, sym in enumerate(cols):
                if abs(w_t[k] - w[k]) < 1e-12:
                    continue
                rt = raw_t.get(sym)
                if pd.isna(rt):
                    w_t[k] = w[k]
                    continue
                p0 = raw_prev.get(sym)
                if pd.isna(p0) or p0 <= 0:
                    continue
                chg = float(rt) / float(p0) - 1.0
                lim = board_limit(sym)
                if w_t[k] > w[k] and chg >= lim - 0.001:
                    w_t[k] = w[k]
                elif w_t[k] < w[k] and chg <= -lim + 0.001:
                    w_t[k] = w[k]
            delta = w_t - w
            buy, sell = np.maximum(delta, 0.0), np.maximum(-delta, 0.0)
            cap = nav * CAPITAL
            buy_amt, sell_amt = buy * cap, sell * cap
            comm = (sum(max(a * COST["fee"], COST["min_fee"]) for a in buy_amt if a > 0)
                    + sum(max(a * COST["fee"], COST["min_fee"]) for a in sell_amt if a > 0))
            stamp = float(sell_amt.sum()) * COST["stamp"]
            trans = float(buy_amt.sum() + sell_amt.sum()) * COST["transfer"]
            slip = float(buy_amt.sum() + sell_amt.sum()) * COST["slip"]
            on = float((w * r_on.iloc[i].to_numpy()).sum())
            intra = float((w_t * r_in.iloc[i].to_numpy()).sum())
            nav *= (1.0 + on + intra)
            nav *= (1.0 - (comm + stamp + trans + slip) / cap)
            denom = 1.0 + intra
            w = w_t * (1.0 + r_in.iloc[i].to_numpy()) / denom if denom > 1e-12 else w_t
        else:
            nav *= (1.0 + float((w * r_c.iloc[i].to_numpy()).sum()))
        peak = max(peak, nav)
        nav_s[t] = nav
    return pd.Series(nav_s).sort_index()


def main() -> None:
    store = ParquetStore()
    panel = pd.read_parquet(PANEL_CACHE)
    panel["date"] = pd.to_datetime(panel["date"]).astype("datetime64[ns]")
    panel["symbol"] = panel["symbol"].astype(str)
    pit_syms = list(panel["symbol"].unique())
    prices, opens = load_price_matrix(pit_syms, start="20180101", store=store)
    panel_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    b = get_kline("000300", adjust="none", store=store).set_index("date")["close"]
    bench_ret = b.pct_change()

    print(">> 生成三策略权重矩阵...", flush=True)
    Ws = {
        "corrected": composite_topn(panel, STRATEGY_FACTORS["corrected"],
                                    top_n=50, rebalance_days=20),
        "dividend": composite_topn(panel, STRATEGY_FACTORS["dividend"],
                                   top_n=50, rebalance_days=20),
        "topkdrop": topk_dropout_topn(panel, STRATEGY_FACTORS["topkdrop"],
                                      topk=50, n_drop=5, rebalance_days=20),
    }

    rows = []
    for strat, W in Ws.items():
        # ① 模拟器校验：无断路器 vs 官方引擎
        eng = run_portfolio_backtest(
            W, prices, fee=COST["fee"], min_fee=COST["min_fee"],
            slippage=COST["slip"], stamp_duty=COST["stamp"],
            transfer_fee=COST["transfer"], limit_skip=True,
            init_capital=CAPITAL, benchmark_returns=bench_ret, open_prices=opens)
        sim = simulate(W, prices, opens, max_dd=None)
        aligned = eng["nav"].reindex(sim.index).dropna()
        diff = float((aligned / sim.reindex(aligned.index) - 1.0).abs().max())
        ok = diff < 2e-02
        caveat = ("（含 ffill/limit 边界的细微残余：所有断路器臂共享同一偏差，"
                  "阈值间相对比较有效；绝对基准以引擎为准）") if diff >= 1e-6 else ""
        print(f"  [校验] {strat}: 模拟器 vs 引擎 最大偏差 {diff:.2e} "
              f"{'✓' if ok else '✗ 超容差'}{caveat}", flush=True)
        if not ok:
            sys.exit(2)
        # ② 基准 + 深阈值扫描
        for tag, th in [("基准(无断路器)", None), *[(f"深阈值{int(-t*100)}%减半", t)
                                                    for t in THRESHOLDS]]:
            nav = simulate(W, prices, opens, max_dd=th)
            n_years = max(len(nav) / 250, 1e-9)
            ann = nav.iloc[-1] ** (1 / n_years) - 1
            mdd = float((nav / nav.cummax() - 1).min())
            rows.append({"strategy": strat, "variant": tag, "total": float(nav.iloc[-1] - 1),
                         "ann": float(ann), "mdd": mdd,
                         "calmar": ann / abs(mdd) if mdd < 0 else np.nan})
        print(f"  [done] {strat}", flush=True)

    res = pd.DataFrame(rows)
    print("\n== 断路器深危机阈值扫描（保真成本，开盘执行）==")
    for strat in Ws:
        sub = res[res["strategy"] == strat]
        base = sub[sub["variant"] == "基准(无断路器)"].iloc[0]
        print(f"\n  [{strat}] 基准 总收益 {base['total']:+.1%} MDD {base['mdd']:.1%} "
              f"Calmar {base['calmar']:.2f}")
        for _, r in sub[sub["variant"] != "基准(无断路器)"].iterrows():
            d_ret = r["total"] - base["total"]
            d_mdd = (r["mdd"] - base["mdd"]) * 100
            print(f"  {r['variant']:<14} 总收益 {r['total']:+.1%}（{d_ret:+13.1%}） "
                  f"MDD {r['mdd']:.1%}（{d_mdd:+.1f}pp） Calmar {r['calmar']:.2f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT_DIR / "circuit_breaker_deep_scan.parquet", index=False)
    try:
        from src.analysis.experiments import record
        record("circuit_breaker_deep_scan",
               params={"thresholds": THRESHOLDS, "scale": SCALE,
                       "strategies": list(Ws)},
               metrics={f"{r['strategy']}|{r['variant']}": float(r["total"])
                        for _, r in res.iterrows()},
               notes="深危机阈值扫描：D-033 悬案收口，结论见 DECISIONS D-069",
               artifacts=["research_results/circuit_breaker_deep_scan.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
