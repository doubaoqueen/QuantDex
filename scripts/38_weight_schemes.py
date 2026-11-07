"""第 38 步：组合内权重方案 A/B——等权 vs 逆波动率 vs 分数加权（D-070）。

现状：所有策略都是 Top-N **等权**——这个选择本身从未被验证。三个候选：
  1) 等权（基线，现行）；
  2) 逆波动率：w_i ∝ 1/σ_i（σ = 信号日 20 日日收益std）——波动小的多配，
     低波家族组合里"顺水推舟"的候选；
  3) 分数加权：w_i ∝ score_i（方向对齐 rank 均值）——分数高的多配。
三套 W 全部走官方组合引擎（保真成本/涨跌停停牌跳过/T+1 开盘），同一面板。
范围：corrected + dividend（topk 的换出逻辑与持仓耦合，权重方案留待后续）。

用法：python scripts/38_weight_schemes.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import STRATEGY_FACTORS, _daily_score, composite_topn

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "research_results"


def reweight(W: pd.DataFrame, scheme: str, prices: pd.DataFrame,
             score_piv: pd.DataFrame | None = None) -> pd.DataFrame:
    """把等权 W 改写为指定权重方案（仅改行内权重分布，选股集合不变）。"""
    out_rows = {}
    ret = prices.pct_change()
    vol20 = ret.rolling(20).std()
    for t, row in W.iterrows():
        names = row[row > 0].index
        if len(names) == 0:
            continue
        if scheme == "equal":
            w = pd.Series(1.0 / len(names), index=names)
        elif scheme == "inv_vol":
            v = vol20.loc[t].reindex(names)
            v = v.fillna(v.median()).clip(lower=1e-4)
            iv = 1.0 / v
            w = iv / iv.sum()
        elif scheme == "score":
            s = score_piv.loc[t].reindex(names).dropna()
            s = s.clip(lower=0.01)
            w = s / s.sum()
        else:
            raise ValueError(scheme)
        out_rows[t] = w
    out = pd.DataFrame(out_rows).T.fillna(0.0)
    out.index.name = "date"
    return out


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

    rows = []
    for strat, factors in [("corrected", STRATEGY_FACTORS["corrected"]),
                           ("dividend", STRATEGY_FACTORS["dividend"])]:
        W_eq = composite_topn(panel, factors, top_n=50, rebalance_days=20)
        score_piv = _daily_score(panel, factors)
        for scheme in ("equal", "inv_vol", "score"):
            W = W_eq if scheme == "equal" else reweight(W_eq, scheme, prices, score_piv)
            res = run_portfolio_backtest(
                W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
                stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
                init_capital=1_000_000.0, benchmark_returns=bench_ret,
                open_prices=opens)
            perf = portfolio_performance(res)
            rows.append({"strategy": strat, "scheme": scheme,
                         "total": perf["总收益率"], "ann": perf["年化收益率"],
                         "mdd": perf["最大回撤"], "calmar": perf["Calmar比率"],
                         "sharpe": perf["夏普比率"], "turnover": perf["年均换手率"],
                         "exc_ir": perf.get("信息比率", np.nan)})
            print(f"  [done] {strat}/{scheme}", flush=True)

    res = pd.DataFrame(rows)
    print("\n== 权重方案 A/B（保真口径，Top50/20日，开盘执行）==")
    print(f"{'策略':<10} {'方案':<10} {'总收益':>9} {'年化':>8} {'MDD':>8} "
          f"{'Calmar':>7} {'夏普':>6} {'超额IR':>7}")
    for _, r in res.iterrows():
        print(f"{r['strategy']:<10} {r['scheme']:<10} {r['total']:>+9.1%} "
              f"{r['ann']:>+8.1%} {r['mdd']:>8.1%} {r['calmar']:>7.2f} "
              f"{r['sharpe']:>6.2f} {r['exc_ir']:>7.2f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_parquet(OUT_DIR / "weight_scheme_ab.parquet", index=False)
    try:
        record("weight_scheme_ab",
               params={"schemes": ["equal", "inv_vol", "score"],
                       "strategies": ["corrected", "dividend"]},
               metrics={f"{r['strategy']}|{r['scheme']}|total": float(r["total"])
                        for _, r in res.iterrows()},
               notes="组合内权重方案 A/B：结论见 DECISIONS D-070",
               artifacts=["research_results/weight_scheme_ab.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
