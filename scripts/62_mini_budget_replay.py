"""第 62 步：小预算回放 vs 体检的等比例/快照口径（D-118 遗留，2026-11-27）。

要回答的问题（用户指认）：开户前体检用"50 只组合的 MDD"当分母算可投入金额，
但小预算实际组不出 50 只。这个差别有多大？分段看会不会更大？

做法：
  1. 取策略历史真实的 20 日调仓权重序列（05 号产物），
  2. 对每个预算档跑 `run_mini_account`（逐期整手 + 最低佣金 + 现金约束 + T+1 开盘），
  3. 与"同权重的组合引擎口径"（等价于体检假设：无整手、无现金约束）对照，
  4. 按 全样本 / 逐年 / 指数涨跌年 分段输出年化、MDD、成本率、持仓数、仓位。

用法：python scripts/62_mini_budget_replay.py [corrected|dividend|topkdrop]
产出：data/store/research_results/mini_budget_replay[_suffix].parquet + 终端对照表
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import FEE_RATE, LOT, MIN_FEE, SLIP_RATE, STAMP_RATE, STORE_DIR, TRANSFER_RATE
from src.backtest.mini_account import run_mini_account, summarize
from src.backtest.portfolio import benchmark_total_return, run_portfolio_backtest
from src.data.market import load_price_matrix

# 预算档：小账户 / L=1万 / 1.5×档 / L=2万推荐 / QMT 门槛 / 中大资金 / 实盘大账户
BUDGETS = (2_000, 20_000, 48_000, 62_480, 100_000, 500_000, 1_000_000)
SUFFIX = {"corrected": "", "dividend": "_div", "topkdrop": "_tk"}
START = "2018-01-01"


def _segments(index: pd.DatetimeIndex, bench: pd.Series):
    """分段定义：全样本 / 逐年 / 指数涨跌年（涨年=指数当年收益>0）。"""
    segs = [("全样本 2018-2026", index)]
    for y in sorted(set(index.year)):
        d = index[index.year == y]
        if len(d) >= 60:
            segs.append((f"{y} 年", d))
    by_year = bench.groupby(bench.index.year).apply(lambda x: (1 + x).prod() - 1)
    for label, mask in (("指数涨年", by_year > 0), ("指数跌年", by_year <= 0)):
        years = set(by_year[mask].index)
        d = index[np.isin(index.year, list(years))]
        if len(d) >= 60:
            segs.append((label, d))
    return segs


def main() -> None:
    strategy = sys.argv[1] if len(sys.argv) > 1 else "corrected"
    if strategy not in SUFFIX:
        raise SystemExit(f"未知策略名 {strategy!r}（合法：{list(SUFFIX)}）——拒绝静默 fallback")
    tag = f"000300_top50_reb20{SUFFIX[strategy]}"
    W = pd.read_parquet(STORE_DIR / "portfolio_results" / f"weights_{tag}.parquet")
    W.index = pd.to_datetime(W.index)
    nav_ref_file = pd.read_parquet(STORE_DIR / "portfolio_results" / f"nav_{tag}.parquet")
    dates = pd.to_datetime(nav_ref_file.iloc[:, 0].index)

    symbols = sorted(set(W.columns))
    prices, opens = load_price_matrix(symbols, start=START, store=None)
    prices, opens = prices.reindex(dates), opens.reindex(dates)
    idx = pd.read_parquet(STORE_DIR / "market" / "daily" / "000300__none.parquet",
                          columns=["date", "close"])
    bench = benchmark_total_return(idx.assign(date=pd.to_datetime(idx["date"]))
                                   .set_index("date")["close"].astype(float)).reindex(dates).fillna(0)
    costs = dict(fee=FEE_RATE, min_fee=MIN_FEE, slippage=SLIP_RATE, stamp_duty=STAMP_RATE,
                 transfer_fee=TRANSFER_RATE)

    print(f">> 策略 {strategy}：{len(W)} 个调仓日 × {len(W.columns)} 只，"
          f"{str(dates[0].date())} ~ {str(dates[-1].date())}", flush=True)
    # 基准：组合引擎口径（= 体检假设的"你买得起 50 只"）
    ref = run_portfolio_backtest(W, prices, **costs, limit_skip=True, init_capital=1_000_000.0,
                                 open_prices=opens, statutory_costs=True)
    ref_nav = ref["nav"]

    rows = []
    navs = {}
    for cap in BUDGETS:
        res = run_mini_account(W, prices, opens, float(cap), **costs)
        navs[cap] = res["nav"]
        pos = res["n_positions"]
        inv = res["invested_ratio"]
        for label, seg in _segments(dates, bench):
            a = summarize(navs[cap].reindex(seg), bench)
            b = summarize(ref_nav.reindex(seg), bench)
            if not a or not b:
                continue
            avg_inv = float(inv.reindex(seg).mean())
            rows.append({
                "budget": cap, "segment": label, "days": a["days"],
                "mini_ann": a["ann"], "mini_mdd": a["mdd"], "mini_vol": a["vol"],
                "ref_ann": b["ann"], "ref_mdd": b["mdd"], "ref_vol": b["vol"],
                "mdd_ratio": a["mdd"] / b["mdd"] if b["mdd"] else np.nan,
                # 仓位归一化：把半仓的实际回撤折算到满仓口径，才能与 50 只策略相比
                "mini_mdd_norm": a["mdd"] / avg_inv if avg_inv > 1e-9 else np.nan,
                "avg_positions": float(pos.reindex(seg).mean()),
                "avg_invested": avg_inv,
                "annual_cost_pct": res["annual_cost_pct"],
                "cost_on_invested": res["annual_cost_pct"] / max(float(inv.mean()), 1e-9),
                "annual_turnover_x": res["annual_turnover_x"],
            })
        print(f"   预算 {cap:>9,} 完成", flush=True)

    df = pd.DataFrame(rows)
    out = STORE_DIR / "research_results" / f"mini_budget_replay{SUFFIX[strategy]}.parquet"
    df.to_parquet(out, index=False)

    full = df[df["segment"] == "全样本 2018-2026"].set_index("budget")
    print("\n=== 全样本：体检口径 vs 实际回放 ===")
    print(f"{'预算':>10}{'平均持仓':>9}{'平均仓位':>9}{'回放年化':>10}{'回放MDD':>9}"
          f"{'满仓折算MDD':>12}{'引擎MDD':>9}{'成本/本金':>10}{'成本/投入':>10}")
    for cap, r in full.iterrows():
        norm = f"{r['mini_mdd_norm']:>12.1%}" if np.isfinite(r["mini_mdd_norm"]) else f"{'—':>12}"
        print(f"{cap:>10,}{r['avg_positions']:>9.1f}{r['avg_invested']:>9.0%}"
              f"{r['mini_ann']:>10.1%}{r['mini_mdd']:>9.1%}{norm}{r['ref_mdd']:>9.1%}"
              f"{r['annual_cost_pct']:>10.1%}{r['cost_on_invested']:>10.1%}")

    print("\n=== 按 L=2 万反算「可投入」（体检公式 可投入 = L ÷ |MDD|）===")
    print(f"{'口径':<34}{'MDD':>9}{'可投入':>12}{'年化':>8}{'平均持仓':>10}")
    mdd_ref = float(full.loc[62_480, "ref_mdd"])
    print(f"{'体检现在用的（50 只组合）':<34}{mdd_ref:>9.1%}{20_000 / abs(mdd_ref):>12,.0f}"
          f"{full.loc[62_480, 'ref_ann']:>8.1%}{'50':>10}")
    for cap, r in full.iterrows():
        mdd = r["mini_mdd_norm"] if np.isfinite(r["mini_mdd_norm"]) else np.nan
        able = f"{20_000 / abs(mdd):>12,.0f}" if np.isfinite(mdd) and mdd != 0 else f"{'不可算':>12}"
        print(f"{f'回放实测（预算 {cap:,} 元）':<34}{r['mini_mdd']:>9.1%}{able}"
              f"{r['mini_ann']:>8.1%}{r['avg_positions']:>10.1f}")

    print("\n=== 分段：满仓折算 MDD ÷ 引擎 MDD（>1 = 同样满仓口径下小预算风险更大）===")
    piv = df.assign(r=df["mini_mdd_norm"] / df["ref_mdd"]).pivot(
        index="segment", columns="budget", values="r")
    order = [s for s in df["segment"].unique()]
    print(piv.reindex(order).round(2).to_string())

    print("\n=== 分段：实际仓位（小预算到底投出去多少）===")
    piv_inv = df.pivot(index="segment", columns="budget", values="avg_invested")
    print((piv_inv.reindex(order) * 100).round(0).to_string())

    print("\n=== 分段：回放年化 - 引擎年化（pp，负 = 小预算跑输）===")
    piv2 = (df.pivot(index="segment", columns="budget", values="mini_ann")
            - df.pivot(index="segment", columns="budget", values="ref_ann")) * 100
    print(piv2.reindex(order).round(1).to_string())
    print(f"\n>> 明细已保存 {out}")


if __name__ == "__main__":
    main()
