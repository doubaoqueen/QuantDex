"""第 33 步：S0 执行形态分年对照（INTRADAY_EXECUTION_DESIGN §5/§8 S0，D-064）。

问题：D-034 只算过全期总数（开盘 vs 收盘偏差最高 ~12pp），**无法看稳定性**。
S0 规格：三策略 × {T+1 开盘执行, T+1 收盘执行} 各跑一遍，分年输出差异表。
统计纪律（§5）：结论必须**分年方向一致**才可采纳；方向混乱的正确结论是
"维持 next_open 不动"——研究结论为"不切换"同样是合格产出（D-022/D-033 先例）。

附带交付：开盘执行口径下的**两腿毛收益分解**（信号日收盘→执行日开盘的隔夜腿
vs 执行日开盘→收盘的日内腿）——回答"这个撮合约定赚的是隔夜的钱还是日内的钱"，
是滑点校准（路线 1）的第一块积木。毛口径未扣成本（成本逐笔在调仓日计，
对两腿的相对结构影响有限），仅看结构不用于决策。

口径：与 05 完全一致（保真成本、涨跌停停牌跳过、100 万名义本金、PIT 全价矩阵）。
产出：research_results/s0_execution_yearly.parquet + 终端表 + 实验存档。
用法：python scripts/33_execution_s0.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import STRATEGY_FACTORS, composite_topn, topk_dropout_topn

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "research_results"
OOS_START = 2022


def build_weights(panel: pd.DataFrame, top_n: int = 50, rebalance: int = 20) -> dict:
    """三策略目标权重（与 05 同构）。"""
    return {
        "corrected": composite_topn(panel, STRATEGY_FACTORS["corrected"],
                                    top_n=top_n, rebalance_days=rebalance),
        "dividend": composite_topn(panel, STRATEGY_FACTORS["dividend"],
                                   top_n=top_n, rebalance_days=rebalance),
        "topkdrop": topk_dropout_topn(panel, STRATEGY_FACTORS["topkdrop"],
                                      topk=top_n, n_drop=5, rebalance_days=rebalance),
    }


def yearly_returns(nav: pd.Series) -> pd.Series:
    """按日历年切的年度收益（年内首末日净值比）。"""
    y = nav.groupby(nav.index.year)
    return (y.last() / y.first() - 1.0).rename("ret")


def leg_decomposition(W: pd.DataFrame, prices: pd.DataFrame,
                      opens: pd.DataFrame) -> dict:
    """开盘执行口径的两腿毛收益分解（无成本近似，仅看结构）。

    复刻引擎的权重语义：隔夜腿=旧权重 × (open_t/close_{t-1}-1)；
    日内腿=调仓日新权重 / 非调仓日当前权重 × (close_t/open_t-1)；权重逐日漂移。
    """
    cols = W.columns
    px = prices[cols].sort_index().ffill()
    op = opens[cols].sort_index().reindex(px.index).ffill()
    dates = px.index
    date_pos = {d: i for i, d in enumerate(dates)}
    eff = {}
    for d in W.index.sort_values():
        if d in date_pos and date_pos[d] + 1 < len(dates):
            eff[dates[date_pos[d] + 1]] = d
    r_on = (op / px.shift(1) - 1.0).fillna(0.0).replace([np.inf, -np.inf], 0.0)
    r_in = (px / op - 1.0).fillna(0.0).replace([np.inf, -np.inf], 0.0)
    w = np.zeros(len(cols))
    leg_on = leg_in = 0.0
    for i in range(1, len(dates)):
        t = dates[i]
        on = float((w * r_on.iloc[i].to_numpy()).sum())
        if t in eff:
            w_t = W.loc[eff[t]].reindex(cols).fillna(0.0).to_numpy(float)
        else:
            w_t = w
        intra = float((w_t * r_in.iloc[i].to_numpy()).sum())
        leg_on += on
        leg_in += intra
        port = on + intra
        denom = 1.0 + port
        if denom > 1e-12:
            w = w_t * (1.0 + r_in.iloc[i].to_numpy()) / denom
        else:
            w = w_t
    return {"overnight_leg": leg_on, "intraday_leg": leg_in,
            "gross_total": leg_on + leg_in}


def main() -> None:
    store = ParquetStore()
    if not PANEL_CACHE.exists():
        print(">> 面板缓存不存在，先跑 30 号（--rebuild 不需要，直接跑会建缓存）")
        sys.exit(2)
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
    Ws = build_weights(panel)

    rows = []
    navs = {}
    for strat, W in Ws.items():
        for style, op in (("open", opens), ("close", None)):
            res = run_portfolio_backtest(
                W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
                stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
                init_capital=1_000_000.0, benchmark_returns=bench_ret,
                open_prices=op)
            yr = yearly_returns(res["nav"])
            navs[(strat, style)] = res["nav"]
            for year, ret in yr.items():
                rows.append({"strategy": strat, "style": style,
                             "year": int(year), "ret": float(ret)})
        perf = portfolio_performance(
            run_portfolio_backtest(
                W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
                stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
                init_capital=1_000_000.0, benchmark_returns=bench_ret,
                open_prices=opens))
        print(f"   [done] {strat}", flush=True)

    df = pd.DataFrame(rows)
    piv = df.pivot_table(index=["strategy", "year"], columns="style",
                         values="ret").reset_index()
    piv["gap_pp"] = (piv["open"] - piv["close"]) * 100
    piv["oos"] = piv["year"] >= OOS_START

    print("\n== S0：开盘 vs 收盘 分年收益（保真口径）==")
    print(f"{'策略':<10} {'年份':<6} {'收盘':>8} {'开盘':>8} {'差异pp':>8}")
    for _, r in piv.sort_values(["strategy", "year"]).iterrows():
        mark = " *" if r["year"] >= OOS_START else ""
        print(f"{r['strategy']:<10} {int(r['year'])}{mark:<5} "
              f"{r['close']:>+8.1%} {r['open']:>+8.1%} {r['gap_pp']:>+8.1f}")

    print("\n== 稳定性判定（统计纪律：分年方向一致才可采纳）==")
    verdict = {}
    for strat in Ws:
        sub = piv[piv["strategy"] == strat]
        n_open_wins = int((sub["gap_pp"] > 0).sum())
        years = len(sub)
        oos_gap = sub[sub["oos"]]["gap_pp"]
        full_gap = sub["gap_pp"]
        verdict[strat] = {
            "years_open_better": f"{n_open_wins}/{years}",
            "full_gap_pp_mean": float(full_gap.mean()),
            "oos_gap_pp_mean": float(oos_gap.mean()),
            "consistent": bool((full_gap > 0).all() or (full_gap < 0).all()),
        }
        v = verdict[strat]
        print(f"  {strat:<10} 开盘占优 {v['years_open_better']} 年 | "
              f"全期平均差 {v['full_gap_pp_mean']:+.1f}pp | "
              f"OOS 平均差 {v['oos_gap_pp_mean']:+.1f}pp | "
              f"分年方向{'一致 ✓' if v['consistent'] else '混乱 ✗ → 维持不切换'}")

    print("\n== 两腿毛收益分解（开盘执行，无成本口径，仅看结构）==")
    for strat, W in Ws.items():
        legs = leg_decomposition(W, prices, opens)
        total = legs["gross_total"]
        share_on = legs["overnight_leg"] / total if total else np.nan
        print(f"  {strat:<10} 隔夜腿 {legs['overnight_leg']:+.1%} | "
              f"日内腿 {legs['intraday_leg']:+.1%} | "
              f"隔夜占比 {share_on:.0%}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    piv.to_parquet(OUT_DIR / "s0_execution_yearly.parquet", index=False)
    try:
        from src.analysis.experiments import record
        record("s0_execution_yearly",
               params={"strategies": list(Ws), "oos_start": OOS_START},
               metrics={f"{k}_full_gap_pp": v["full_gap_pp_mean"]
                        for k, v in verdict.items()},
               notes="S0 分年对照：结论见 DECISIONS D-064；纪律=分年一致才可采纳",
               artifacts=["research_results/s0_execution_yearly.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
