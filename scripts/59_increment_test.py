"""第 59 步：D-105 幸存者增量检验（预注册版，2026-11-22，先冻结后运行）。

背景（D-105）：枚举挖矿 481 候选 → 幸存者全负 IC，归并为三家族。其中
**量价相关**（Corr($close,$turnover,20)，IC -0.065 / NW-t -5.00 / q 0.0003）与
**换手波动**（Std($turnover,20)，IC -0.075 / NW-t -4.44 / q 0.0006）与现行三因子
秩相关仅 0.06~0.24——本条检验它们对 corrected 主线是否有**增量**。

变体（预注册，四臂，A 为现行 canonical）：
  A baseline：corrected 三因子 = turnover_20/vol_20/rev_5（全部 -1）
  B：A + pcorr_20（Corr($close,$turnover,20)，-1）
  C：A + tstd_20（Std($turnover,20)，-1）
  D：A + 两者
协议：与 06 号完全同款（PIT 949 / 剔 ST / 2018+ / D-034 开盘执行 / 生产成本 /
参数网格 3×3 训练段 IR 选参 → 逐年样本外 2019-2026 链式）。

**裁决标准（预注册，先于运行冻结；四臂多重比较的通胀由 +1pp/+0.05IR 硬门槛控制）**：
  ① WF OOS 年化增量 ≥ +1.0pp 且 IR 增量 ≥ +0.05（相对 A，同一协议同代数据）
  ② 分年 OOS：变体年收益 ≥ baseline 的年数 ≥ 5/8
  ③ 全样本 canonical（Top50/20 日，05 口径）：MDD 恶化 ≤ 1.0pp 且总收益不降
  ④ ①② 全过者 → 以 ③ 复核 → 全过才提案进影子盘增量检验（不直接换 canonical）；
    任一不过 → 该家族**增量 = 0，入档**（与 0/46 基率并列）。
红线：不碰进攻型六格；不挤占 12-09/12-29 主线（空闲时段跑）；影子盘账户池
  不因此变更（提案 ≠ 采纳）。
"""
import sys
from itertools import product

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR, TRADING_DAYS_PER_YEAR
from src.analysis.experiments import record
from src.analysis.factors import build_factor_panel
from src.backtest.portfolio import run_portfolio_backtest
from src.data import universe
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import _daily_score, composite_topn

START_DATE = "20180101"
TEST_YEARS = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
GRID = [(t, r) for t in (20, 30, 50) for r in (10, 20, 60)]
BASE3 = [("turnover_20", -1), ("vol_20", -1), ("rev_5", -1)]
EXPR = {"pcorr_20": "Corr($close, $turnover, 20)",
        "tstd_20": "Std($turnover, 20)"}
VARIANTS = {  # 预注册冻结，勿增删
    "A_baseline": BASE3,
    "B_pcorr20": BASE3 + [("pcorr_20", -1)],
    "C_tstd20": BASE3 + [("tstd_20", -1)],
    "D_both": BASE3 + [("pcorr_20", -1), ("tstd_20", -1)],
}
OUT = STORE_DIR / "research_results" / "increment_test.json"

_W_CACHE: dict = {}


def _weights(panel: pd.DataFrame, key: str, factors, combo) -> pd.DataFrame:
    ck = (key, combo)
    if ck not in _W_CACHE:
        _W_CACHE[ck] = composite_topn(panel, factors, top_n=combo[0],
                                      rebalance_days=combo[1])
    return _W_CACHE[ck]


def metrics_of(res: dict, bench_ret: pd.Series) -> dict:
    r = res["nav"].pct_change().dropna()
    bench = bench_ret.reindex(r.index).dropna()
    ex = (r - bench).dropna()
    # 深检修复（2026-11-22）：年化基数从 244 改为全仓统一的 TRADING_DAYS_PER_YEAR
    # （250）——脚本声称"与 06 号完全同款"，但 06 用 250、此处曾用 244，两边
    # IR/年化不可直接横比。修复后历史存档 increment_test.json 的数字需重跑刷新。
    ann = (res["nav"].iloc[-1] / res["nav"].iloc[0]) ** (TRADING_DAYS_PER_YEAR / max(len(r), 1)) - 1
    ir = (ex.mean() / ex.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
          if ex.std() > 0 else np.nan)
    mdd = (res["nav"] / res["nav"].cummax() - 1).min()
    return {"ann": ann, "ir": ir, "mdd": mdd}


def run_combo(panel, prices, opens, key, factors, combo, dates, bench_ret):
    """与 06 号 run_combo 同款语义：只传调仓日行（引擎 ffill 补洞），
    D-051 首日建仓（_daily_score 支持任意因子表，变体 B/C/D 可用）。"""
    p = prices.loc[dates]
    W = _weights(panel, key, factors, combo)
    W = W[W.index.isin(p.index)]
    if W.empty:
        return None
    first = p.index[0]
    if W.index[0] > first:
        sc = _daily_score(panel, factors)
        sc = sc[sc.index <= first]
        if len(sc):
            s = sc.loc[sc.index[-1]].dropna().sort_values(ascending=False).head(combo[0])
            if len(s):
                row = pd.Series(1.0 / len(s), index=[str(x) for x in s.index])
                W = pd.concat([pd.DataFrame([row], index=[first]), W])
    return run_portfolio_backtest(
        W, p, fee=0.00025, min_fee=5.0, slippage=0.0005,
        stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
        init_capital=1_000_000.0,
        open_prices=opens.reindex(p.index))  # D-034 开盘执行


def main() -> None:
    store = ParquetStore()
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 构建面板（{len(symbols)} 只 + 2 个表达式因子）...", flush=True)
    panel = build_factor_panel(store, symbols, start=START_DATE,
                               exclude_st=True, pit_constituents=pit,
                               expr_factors=EXPR)
    missing = [c for c, _ in sum(VARIANTS.values(), []) if c not in panel.columns]
    if missing:
        print(f"面板缺因子列: {missing}"); sys.exit(2)
    prices, opens = load_price_matrix(symbols, start=START_DATE, store=store)
    panel_dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel["date"]).unique()))
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{len(panel)} 行；价格矩阵 {prices.shape}", flush=True)
    bench = get_kline("000300", adjust="none", store=store)
    bench_ret = bench.set_index("date")["close"].pct_change().rename("bench")

    all_dates = prices.index
    results = {}
    for key, factors in VARIANTS.items():
        folds = []
        oos_navs = []
        for y in TEST_YEARS:
            y0, y1 = pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 1}-01-01")
            train_dates = all_dates[all_dates < y0]
            test_dates = all_dates[(all_dates >= y0) & (all_dates < y1)]
            if len(test_dates) < 30:
                continue
            best, best_ir = None, -np.inf
            for combo in GRID:
                res = run_combo(panel, prices, opens, key, factors, combo,
                                train_dates, bench_ret)
                if res is None:
                    continue
                m = metrics_of(res, bench_ret)
                if np.isfinite(m["ir"]) and m["ir"] > best_ir:
                    best_ir, best = m["ir"], combo
            if best is None:
                continue
            res_oos = run_combo(panel, prices, opens, key, factors, best,
                                test_dates, bench_ret)
            if res_oos is None:
                continue
            m = metrics_of(res_oos, bench_ret)
            folds.append({"year": y, "combo": list(best),
                          "oos_ann": m["ann"], "oos_ir": m["ir"], "oos_mdd": m["mdd"]})
            nav = res_oos["nav"]
            oos_navs.append(nav / nav.iloc[0])
            print(f"   [{key}] {y} {best} OOS年化 {m['ann']:+.2%}", flush=True)
        # 年度链式：各折 nav 已归一到折首=1.0，按年水平拼接（不得再 cumprod 日收益，
        # 否则日收益率被复利一遍，得到天文数字——首版教训）
        level, pieces = 1.0, []
        for nav_k in oos_navs:
            pieces.append(nav_k * level)
            level *= float(nav_k.iloc[-1])
        chained = pd.concat(pieces).groupby(level=0).last().sort_index()
        yrs = (chained.index[-1] - chained.index[0]).days / 365.25
        total = chained.iloc[-1] - 1.0
        ann = (1 + total) ** (1 / yrs) - 1
        r = chained.pct_change().dropna()
        ex = (r - bench_ret.reindex(r.index)).dropna()
        ir = (ex.mean() / ex.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
              if ex.std() > 0 else np.nan)
        mdd = (chained / chained.cummax() - 1).min()
        results[key] = {"folds": folds, "oos_total": total, "oos_ann": ann,
                        "oos_ir": ir, "oos_mdd": mdd,
                        "n_combos_used": len({tuple(f["combo"]) for f in folds})}
        print(f"[{key}] OOS: 总 {total:+.2%} 年化 {ann:+.2%} IR {ir:+.2f} "
              f"MDD {mdd:.2%} 参数 {results[key]['n_combos_used']} 种\n", flush=True)

    # ---- 预注册裁决 ----
    base = results["A_baseline"]
    verdict = {}
    for key in ("B_pcorr20", "C_tstd20", "D_both"):
        v = results[key]
        c1 = (v["oos_ann"] - base["oos_ann"] >= 0.01
              and v["oos_ir"] - base["oos_ir"] >= 0.05)
        by = {f["year"]: f["oos_ann"] for f in v["folds"]}
        bb = {f["year"]: f["oos_ann"] for f in base["folds"]}
        win = sum(1 for y in by if by[y] >= bb.get(y, np.nan))
        c2 = win >= 5
        verdict[key] = {"c1_ann_ir": c1, "c2_yearly": c2, "win_years": f"{win}/{len(by)}",
                        "delta_ann": v["oos_ann"] - base["oos_ann"],
                        "delta_ir": v["oos_ir"] - base["oos_ir"]}
    print("== 预注册裁决（①②关）==")
    for k, vv in verdict.items():
        print(f"   {k}: Δ年化 {vv['delta_ann']:+.2%} ΔIR {vv['delta_ir']:+.2f} "
              f"逐年胜 {vv['win_years']} → ①{vv['c1_ann_ir']} ②{vv['c2_yearly']}")
    survivors = [k for k, vv in verdict.items() if vv["c1_ann_ir"] and vv["c2_yearly"]]
    print(f"\n== 过①②: {survivors or '无'} ==")
    print("③ 全样本 canonical 复核：仅对过①②者另行运行（05 口径），本脚本不自动换档")
    import json
    json.dump({"variants": results, "verdict": verdict,
               "survivors_12": survivors},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    try:
        record("d105_increment_test", params={"variants": list(VARIANTS)},
               metrics={k: v["oos_ann"] for k, v in results.items()},
               notes=f"D-105 增量检验（①②关）：过闸 {survivors or '无'}——③复核另跑",
               artifacts=["research_results/increment_test.json"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] {e}")


if __name__ == "__main__":
    main()
