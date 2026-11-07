"""第 32 步：周期股研究（D-063，用户 2026-11-07 提出）。

用户问题：很多标的有周期性，但周期不稳定——能否重点关注、做成策略？
纪律与 D-061 相同：先测试给结论，再决定是否开账户。

三个层次的检验：
  1) 周期结构检测（"周期存不存在"）：每只股票的去趋势对数价格做 FFT 找主周期
     （限定 60~1000 日），并把样本切两半对比主周期稳定性——直接量化"周期稳不稳"；
     按行业分组（周期 vs 防御 vs 其他）汇总，检验"周期行业更呈周期结构"的先验。
  2) 周期相位因子 IC（"相位能否预测收益"）：52 周位置/中期动量等已有因子 +
     三个新算的周期位置因子（500 日区间分位 range_pos_500、半周期振荡器 dpo_120、
     年度乖离 ma250_bias），在周期股池 / 防御池 / 全市场分别测 RankIC（D-038
     可成交口径 + NW-t）。全市场动量不显著（D-036），条件于周期行业是否成立
     是本轮核心假设。
  3) 若有信号存活（|IC20|>0.03 且 |NW-t|>2）→ 保真口径组合回测 vs 基线。

行业打标（申万一级，静态标签）：
  周期 = 有色金属/煤炭/石油石化/钢铁/基础化工/建筑材料/农林牧渔/汽车
  防御 = 食品饮料/医药生物/公用事业/银行
  已知口径：行业标签为当前快照、回溯全历史（免费源无行业 PIT，D-021 同款限制），
  对"行业间对比"结论影响有限，对"行业内选股"有轻度前视——结论若成立需在
  实盘中用"当前是周期行业"的口径执行（与回测一致，无前视）。

用法：python scripts/32_cycle_research.py
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factor_test import (
    _newey_west_tstat, add_forward_returns, rankic_series,
)
from src.data import universe
from src.data.market import get_kline
from src.data.schema import kline_file_name
from src.data.storage import ParquetStore
from src.strategies.multi_factor import STRATEGY_FACTORS, composite_topn

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "research_results"

CYCLICAL = {"有色金属", "煤炭", "石油石化", "钢铁", "基础化工",
            "建筑材料", "农林牧渔", "汽车"}
DEFENSIVE = {"食品饮料", "医药生物", "公用事业", "银行"}
CYCLE_FACTORS = ["AL2_52W", "mom_60", "AL2_MOM121", "ma60_bias",
                 "range_pos_500", "dpo_120", "ma250_bias"]
TOP_N = 30          # 周期池较小，Top30 等权
REBALANCE = 20


# ---------------------------------------------------------------- 周期因子
def add_cycle_factors(store: ParquetStore, panel: pd.DataFrame) -> pd.DataFrame:
    """逐标的全史计算周期位置因子，按 (date, symbol) 并回面板（不 rank，先原值）。"""
    syms = panel["symbol"].astype(str).unique().tolist()
    frames = []
    for sym in syms:
        name = kline_file_name(sym, "qfq")
        if not store.exists("daily", name):
            continue
        k = store.read("daily", name, columns=["date", "close"])
        c = pd.to_numeric(k["close"], errors="coerce")
        lo = c.rolling(500, min_periods=250).min()
        hi = c.rolling(500, min_periods=250).max()
        rng = (hi - lo).replace(0, np.nan)
        f = pd.DataFrame({
            "date": pd.to_datetime(k["date"]),
            "symbol": str(sym),
            "range_pos_500": ((c - lo) / rng).to_numpy(),
            "dpo_120": (c - c.rolling(120).mean().shift(60)).to_numpy() / c,
            "ma250_bias": (c / c.rolling(250, min_periods=200).mean() - 1).to_numpy(),
        })
        frames.append(f)
    cyc = pd.concat(frames, ignore_index=True)
    panel = panel.merge(cyc, on=["date", "symbol"], how="left")
    # 与面板其他因子同口径：逐日截面 rank（0~1）
    for col in ("range_pos_500", "dpo_120", "ma250_bias"):
        panel[col] = panel.groupby("date")[col].rank(pct=True)
    panel[["range_pos_500", "dpo_120", "ma250_bias"]] = \
        panel[["range_pos_500", "dpo_120", "ma250_bias"]].astype("float32")
    return panel


# ---------------------------------------------------------------- 周期结构诊断
def cycle_structure(store: ParquetStore, ind_map: dict) -> pd.DataFrame:
    """每只股票：去趋势对数价格的 FFT 主周期（60~1000 日）+ 前后半样本稳定性。"""
    rows = []
    for sym, ind in ind_map.items():
        name = kline_file_name(sym, "qfq")
        if not store.exists("daily", name):
            continue
        k = store.read("daily", name, columns=["date", "close"])
        c = pd.to_numeric(k["close"], errors="coerce").dropna()
        if len(c) < 750:  # 至少 3 年
            continue
        lp = np.log(c.to_numpy(float))

        def _dominant(x: np.ndarray) -> tuple[float, float]:
            """返回 (主周期[日], 主周期方差占比)。去趋势=减 250 日均值。"""
            if len(x) < 400:
                return np.nan, np.nan
            with np.errstate(divide="ignore", invalid="ignore"):
                kernel = np.ones(250) / 250
                det = x - np.convolve(x, kernel, mode="same")
                det[: 125] = det[-125:] = 0.0  # 卷积边缘伪影
                spec = np.abs(np.fft.rfft(det)) ** 2
                freqs = np.fft.rfftfreq(len(det))
                mask = (freqs > 0) & (1 / freqs <= 1000) & (1 / freqs >= 60)
                if not mask.any() or spec.sum() <= 0:
                    return np.nan, np.nan
                i = int(np.argmax(np.where(mask, spec, 0.0)))
                period = 1 / freqs[i]
                share = float(spec[i] / spec[mask].sum())
            return float(period), share

        p_full, s_full = _dominant(lp)
        half = len(lp) // 2
        p1, _ = _dominant(lp[:half])
        p2, _ = _dominant(lp[half - 250:])  # 重叠 250 日均线预热
        stab = (1 - min(p1, p2) / max(p1, p2)) if (p1 and p2 and not np.isnan(p1)
                                                   and not np.isnan(p2)) else np.nan
        rows.append({"symbol": sym, "industry": ind,
                     "group": ("cyclical" if ind in CYCLICAL else
                               "defensive" if ind in DEFENSIVE else "other"),
                     "period": p_full, "power_share": s_full,
                     "period_h1": p1, "period_h2": p2, "stability": stab})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- IC 检验
def ic_table(panel: pd.DataFrame, pools: dict[str, pd.Index]) -> pd.DataFrame:
    rows = []
    for pool_name, syms in pools.items():
        sub = panel[panel["symbol"].astype(str).isin(syms)].copy()
        sub = add_forward_returns(sub, horizons=(5, 20))
        for f in CYCLE_FACTORS:
            for h in (5, 20):
                ic = rankic_series(sub, f, h)
                ic = ic.replace([np.inf, -np.inf], np.nan).dropna()
                if len(ic) < 100:
                    continue
                rows.append({
                    "pool": pool_name, "factor": f, "h": h,
                    "ic": float(ic.mean()),
                    "nw_t": float(_newey_west_tstat(ic, h - 1)),
                    "days": int(len(ic)),
                    "icir": float(ic.mean() / ic.std()) if ic.std() > 0 else np.nan,
                })
        print(f"   [ic] {pool_name}: {len(sub)} 行", flush=True)
    return pd.DataFrame(rows)


def main() -> None:
    store = ParquetStore()
    ind_df = store.read("meta", "industry_sw")
    ind_map = dict(zip(ind_df["symbol"].astype(str), ind_df["industry"]))

    panel = pd.read_parquet(PANEL_CACHE)
    panel["date"] = pd.to_datetime(panel["date"]).astype("datetime64[ns]")
    panel["symbol"] = panel["symbol"].astype(str)

    # ---- 1) 周期结构 ----
    print(">> [1/3] 周期结构检测（FFT 主周期 + 前后半样本稳定性）...", flush=True)
    struct = cycle_structure(store, ind_map)
    g = struct.groupby("group")
    summary = g.agg(
        n=("symbol", "size"),
        median_period=("period", "median"),
        median_power_share=("power_share", "median"),
        median_stability=("stability", "median"),
        stable_ratio=("stability", lambda s: float((s > 0.5).mean())),
    )
    print(summary.to_string())
    # 周期行业的强势样本：稳定主周期 + 主周期占比高
    cyc = struct[struct["group"] == "cyclical"]
    strong = cyc[(cyc["stability"] > 0.5) & (cyc["power_share"] > 0.15)]
    print(f"\n   周期行业 {len(cyc)} 只中『主周期稳定(stability>0.5)且能量占比>15%』"
          f"的 {len(strong)} 只：")
    if len(strong):
        for _, r in strong.sort_values("power_share", ascending=False).head(10).iterrows():
            print(f"   {r['symbol']} {r['industry']:<5} 主周期 {r['period']:.0f} 日 "
                  f"占比 {r['power_share']:.0%} 稳定性 {r['stability']:.2f}")

    # ---- 2) 周期相位因子 IC ----
    print(">> [2/3] 周期相位因子 IC（周期池 / 防御池 / 全市场）...", flush=True)
    panel = add_cycle_factors(store, panel)
    syms_cyc = [s for s in panel["symbol"].unique() if ind_map.get(s) in CYCLICAL]
    syms_def = [s for s in panel["symbol"].unique() if ind_map.get(s) in DEFENSIVE]
    print(f"   周期池 {len(syms_cyc)} 只 / 防御池 {len(syms_def)} 只", flush=True)
    tab = ic_table(panel, {
        "cyclical": pd.Index(syms_cyc),
        "defensive": pd.Index(syms_def),
        "all": pd.Index(panel["symbol"].unique()),
    })
    tab["sig"] = (tab["ic"].abs() > 0.03) & (tab["nw_t"].abs() > 2)
    tab = tab.sort_values(["pool", "h", "nw_t"], key=lambda s: s.abs() if s.name == "nw_t" else s)
    print(tab.to_string(index=False, float_format=lambda v: f"{v:+.4f}"
                        if isinstance(v, float) else str(v)))
    strong_ic = tab[tab["sig"] & (tab["h"] == 20)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    struct.to_parquet(OUT_DIR / "cycle_structure.parquet", index=False)
    tab.to_parquet(OUT_DIR / "cycle_ic_summary.parquet", index=False)

    # ---- 3) 有存活信号才进组合回测 ----
    # D-063 修正：回测必须落在"信号显著的池"里（首版误把防御池的显著信号
    # 拿到周期池回测）；每个池补 OOS(2022+) 切片——防御池动量疑似 2019-2021
    # 抱团行情的遗产，OOS 是照妖镜。
    if strong_ic.empty:
        print("\n>> 结论：20 日口径无存活信号（|IC|>0.03 且 |NW-t|>2），"
              "按纪律不进组合回测、不开账户。明细已落 research_results/。")
        sys.exit(0)

    print(f"\n>> [3/3] 存活信号按池回测（保真口径 + OOS 2022+）...", flush=True)
    from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest
    from src.data.market import get_kline, load_price_matrix
    pit_syms = universe.pit_union_symbols("000300", store)
    prices, opens = load_price_matrix(pit_syms, start="20180101", store=store)
    panel_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    b = get_kline("000300", adjust="none", store=store).set_index("date")["close"]
    bench_ret = b.pct_change()
    oos_start = pd.Timestamp("2022-01-01")

    def _run_pool(tag: str, W: pd.DataFrame) -> None:
        res = run_portfolio_backtest(
            W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
            stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
            init_capital=1_000_000.0, benchmark_returns=bench_ret,
            open_prices=opens)
        perf = portfolio_performance(res)
        nav = res["nav"]
        oos = nav.index >= oos_start
        oos_txt = ""
        if oos.any():
            n2 = nav[oos]
            oos_ret = float(n2.iloc[-1] / n2.iloc[0] - 1)
            dd2 = float((n2 / n2.cummax() - 1).min())
            oos_txt = f" | OOS {oos_ret:+.1%} MDD {dd2:.1%}"
        print(f"  {tag:<30} 全期 {perf['总收益率']:+.1%} 年化 {perf['年化收益率']:+.1%} "
              f"MDD {perf['最大回撤']:.1%} 换手 {perf['年均换手率']:.0%}{oos_txt}",
              flush=True)

    pool_specs = [("defensive", syms_def, TOP_N), ("cyclical", syms_cyc, TOP_N),
                  ("all", list(panel["symbol"].unique()), 50)]
    for pool_name, syms_pool, topn in pool_specs:
        cells = tab[(tab["pool"] == pool_name) & tab["sig"] & (tab["h"] == 20)]
        if cells.empty:
            continue
        sub = panel[panel["symbol"].isin(syms_pool)].copy()
        for _, r in cells.iterrows():
            f, d = r["factor"], (1 if r["ic"] > 0 else -1)
            W = composite_topn(sub, [(f, d)], top_n=topn, rebalance_days=REBALANCE)
            _run_pool(f"[{pool_name}]{f}({'多' if d > 0 else '空'})Top{topn}", W)
    # 同窗口基线：corrected（全市场池）
    W0 = composite_topn(panel, STRATEGY_FACTORS["corrected"],
                        top_n=50, rebalance_days=REBALANCE)
    _run_pool("corrected基线(全市场Top50)", W0)


if __name__ == "__main__":
    main()
