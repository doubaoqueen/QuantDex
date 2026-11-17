"""第 45 步：进攻型账户假设的三关检验（D-085，用户 2026-11-17 提案"总得有进攻型账户"）。

假设（配方冻结，参数不扫——与现行账户同构 50/20，无过拟合空间）：
  进攻型 ≠ 现行策略取负号（动量/高换手/高波在自家面板全是负 IC，见 ic_summary）。
  进攻型 = "夹缝中的相对强者"：
    RS60   = 个股 60 日收益 - 市场累计收益（相对强度：大盘跌它不跌）
    RMOM60 = 残余动量（Blitz 式：对市场 120 日 beta 回归的残差，其 60 日
             均值/标准差 = 残差信息比率——剔除市场成分后的持续性）
    TURND  = 换手温和抬升（Mean(turnover,5)/Mean(turnover,60)-1，量能趋势）

三关（39 号闸门阈值，D-073/D-042 纪律）：
  关 1 去重：配方内部 |corr|>0.7 簇只留 |IC20| 最强者；
  关 2 强度：|IC20| ≥ 0.03 且全样本 NW-t ≥ 2（20 日前瞻，D-038 可成交口径）；
  关 3 稳定性：分年 IC 方向一致 ≥ 6/7 年（2020-2026）且近 60 日 IC 不反向。
  组合关：幸存者等权 rank 合成 → 生产成本回测（fee/印花/滑点/开盘执行全开）
          vs 现行 corrected 同口径对照 + 000300 基准。

口径：PIT 成分 + D-071 ST 剔除（生产口径，与候选池一致）；面板起始 20180101。
产出：research_results/offensive_gate.json + experiments 存档。
裁决：过闸 → 注册表第 5 账户；不过 → "已决策不做"清单（与防御性减仓家族并列）。
"""
import json
import sys
from math import erfc, sqrt

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.expr import evaluate_wide
from src.analysis.factor_test import (
    _newey_west_tstat,
    add_forward_returns,
    rankic_series,
)
from src.analysis.factors import build_factor_panel
from src.backtest.portfolio import run_portfolio_backtest
from src.data import universe
from src.data.market import load_price_matrix
from src.data.schema import kline_file_name
from src.data.storage import ParquetStore
from src.strategies.multi_factor import composite_topn

START_DATE = "20180101"
HORIZON = 20                      # D-042 预设单窗口：与 20 日调仓纪律同频
MIN_IC, MIN_T = 0.03, 2.0         # 39 号关 2 阈值
MIN_YEARS = 6                     # 39 号关 3 阈值（2020~2026 共 7 年）
TOP_N, REBAL = 50, 20             # 参数先验冻结（与 corrected 同构）

OFFENSIVE = {
    "RS60":   "$close / Ref($close, 60) - Sum($mkt, 60)",
    "RMOM60": ("Mean($ret - (Cov($ret, $mkt, 120) / Cov($mkt, $mkt, 120)) * $mkt, 60)"
               " / Std($ret - (Cov($ret, $mkt, 120) / Cov($mkt, $mkt, 120)) * $mkt, 60)"),
    "TURND":  "Mean($turnover, 5) / Mean($turnover, 60) - 1",
}
LIVE_FACTORS = [("turnover_20", -1), ("vol_20", -1), ("rev_5", -1)]  # corrected 同构


def build_panel_with_offensive(store: ParquetStore) -> pd.DataFrame:
    """PIT+ST 面板（现任因子 rank）+ 宽面板求值的三个进攻型表达式（含 $mkt 字段）。"""
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    print(f">> 构建 PIT 面板（{len(symbols)} 只，ST 剔除，现任 3 因子）...", flush=True)
    panel = build_factor_panel(store, symbols, start=START_DATE, pit_constituents=pit,
                               factor_names=["turnover_20", "vol_20", "rev_5"],
                               exclude_st=True)
    panel = add_forward_returns(panel, horizons=(HORIZON,))

    close = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    # 原始换手矩阵：面板只保留 rank 后的因子列，原始 turnover 直接从仓库列裁剪读
    tframes = []
    for s in symbols:
        try:
            t = store.read("daily", kline_file_name(str(s), "qfq"), columns=["date", "turnover"])
        except Exception:  # noqa: BLE001  缺文件与面板口径一致（面板行不存在）
            continue
        t["symbol"] = str(s)
        tframes.append(t)
    tall = pd.concat(tframes, ignore_index=True)
    turn = tall.pivot(index="date", columns="symbol", values="turnover").sort_index()
    turn.index = pd.to_datetime(turn.index)
    idx = store.read("daily", "000300__none", columns=["date", "close"])
    mkt_ret = (idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"]
               .pct_change().reindex(close.index))
    mkt = pd.DataFrame(np.tile(mkt_ret.to_numpy()[:, None], (1, close.shape[1])),
                       index=close.index, columns=close.columns)
    fields = {"close": close, "turnover": turn, "mkt": mkt,
              "ret": close.pct_change()}
    print(f">> 宽面板求值进攻型表达式 {len(OFFENSIVE)} 个...", flush=True)
    for name, expr in OFFENSIVE.items():
        wide = evaluate_wide(expr, fields)
        long = wide.stack().rename(name).reset_index()
        long.columns = ["date", "symbol", name]
        long["symbol"] = long["symbol"].astype(str)
        panel = panel.merge(long, on=["date", "symbol"], how="left")
        panel[name] = panel.groupby("date")[name].rank(pct=True)   # 与 build_factor_panel 同口径
    return panel


def gate1_strength(panel: pd.DataFrame) -> pd.DataFrame:
    """关 2 强度 + 批内 FDR（D-042）+ 关 3 分年一致性。"""
    rows = []
    for f in OFFENSIVE:
        s = rankic_series(panel, f, HORIZON).dropna()
        ic, t = float(s.mean()), float(_newey_west_tstat(s, HORIZON - 1))
        years = s.groupby(s.index.year).mean()
        yy = years.loc[years.index >= 2020]
        sign = np.sign(ic)
        n_consistent = int((np.sign(yy) == sign).sum())
        near = float(s.iloc[-60:].mean()) if len(s) >= 60 else np.nan
        rows.append({"factor": f, "days": int(len(s)), "ic20": ic, "nw_t": t,
                     "years_consistent": n_consistent, "n_years": int(len(yy)),
                     "ic_recent60": near, "near_ok": bool(np.sign(near) == sign),
                     "gate2": bool(abs(ic) >= MIN_IC and abs(t) >= MIN_T),
                     "gate3": bool(n_consistent >= MIN_YEARS and np.sign(near) == sign)})
        print(f"   {f:<7} IC20 {ic:+.4f} NW-t {t:+.2f}  分年一致 {n_consistent}/{len(yy)}"
              f"  近60日IC {near:+.4f}", flush=True)
    out = pd.DataFrame(rows)
    out["p20"] = [erfc(abs(v) / sqrt(2)) if pd.notna(v) else np.nan for v in out["nw_t"]]
    m = out["p20"].notna().sum()
    pv = out.loc[out["p20"].notna(), "p20"].sort_values()
    out["q20"] = (pv * m / pv.rank(method="first")).clip(upper=1.0).reindex(out.index)
    return out


def gate1_dedup(panel: pd.DataFrame, strength: pd.DataFrame) -> list[str]:
    """关 1 去重：配方内部 |corr|>0.7 簇留 |IC20| 最强者（39 号规则）。"""
    order = strength.sort_values("ic20", key=lambda s: s.abs(), ascending=False)["factor"]
    cols = panel[[*OFFENSIVE]].dropna()
    corr = cols.corr(method="spearman")
    print("\n[关1] 配方内相关矩阵：")
    print(corr.round(2).to_string())
    kept, absorbed = [], []
    for f in order:
        if any(abs(corr.loc[f, k]) > 0.7 for k in kept):
            absorbed.append(f)
        else:
            kept.append(f)
    if absorbed:
        print(f"   去重：吸收 {absorbed}（与 {kept} 相关>0.7）")
    else:
        print("   去重：无簇，全部保留")
    # 与现任因子的相关（信息性：进攻型到底有多"不同于现行"）
    live = panel[["turnover_20", "vol_20", "rev_5", *OFFENSIVE]].dropna().corr(method="spearman")
    print("\n[对照] 与现任因子的相关（进攻型 vs corrected 原材料）：")
    print(live.loc[OFFENSIVE.keys(), ["turnover_20", "vol_20", "rev_5"]].round(2).to_string())
    return kept


def run_strategy(panel: pd.DataFrame, factors: list[tuple[str, int]], prices, opens,
                 bench: pd.Series, label: str) -> dict:
    """冻结参数 50/20 的组合回测（生产成本全开：D-034 开盘执行）。"""
    W = composite_topn(panel, factors, top_n=TOP_N, rebalance_days=REBAL)
    res = run_portfolio_backtest(
        W, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
        stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
        init_capital=1_000_000.0, open_prices=opens.reindex(prices.index))
    nav, ret = res["nav"], res["daily_ret"]
    exc = ret - bench.reindex(ret.index).fillna(0.0)
    n = len(ret)
    metrics = {
        "ann": float(nav.iloc[-1] ** (250 / n) - 1) if n else np.nan,
        "ir": float(exc.mean() / exc.std() * np.sqrt(250)) if exc.std() > 0 else np.nan,
        "mdd": float((nav / nav.cummax() - 1).min()),
        "total": float(nav.iloc[-1] - 1),
        "excess_ann": float((1 + exc.mean()) ** 250 - 1) if n else np.nan,
        "turnover_total": float(res.get("turnover", 0) or 0),
        "cost_total": float(res["summary"]["total"]) if res.get("summary") else None,
    }
    yearly = {}
    for y, g in ret.groupby(ret.index.year):
        gn = (1 + g).cumprod()
        nb = (1 + bench.reindex(g.index).fillna(0.0)).cumprod()
        e = g - bench.reindex(g.index).fillna(0.0)
        yearly[str(y)] = {"ann": float(gn.iloc[-1] - 1),
                          "bench": float(nb.iloc[-1] - 1),
                          "ir": float(e.mean() / e.std() * np.sqrt(250)) if e.std() > 0 else None}
    metrics["yearly"] = yearly
    print(f"\n[{label}] 年化 {metrics['ann']:+.2%} | 超额年化 {metrics['excess_ann']:+.2%}"
          f" | IR {metrics['ir']:+.2f} | MDD {metrics['mdd']:+.2%}"
          f" | 成本合计 {metrics['cost_total'] or 0:,.0f} 元")
    for y, v in yearly.items():
        print(f"    {y}: {v['ann']:+.2%}（基准 {v['bench']:+.2%}，IR {v['ir']}）")
    return {"metrics": metrics, "res": res}


def main() -> None:
    store = ParquetStore()
    panel = build_panel_with_offensive(store)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，{len(panel)} 行")

    print("\n== 关 2+3：强度与稳定性（20 日前瞻，D-038 口径）==")
    strength = gate1_strength(panel)
    print(strength[["factor", "ic20", "nw_t", "q20", "years_consistent",
                    "ic_recent60", "gate2", "gate3"]].to_string(index=False))

    print("\n== 关 1：去重 ==")
    kept = gate1_dedup(panel, strength)

    survivors = [f for f in kept
                 if bool(strength.set_index("factor").loc[f, "gate2"])
                 and bool(strength.set_index("factor").loc[f, "gate3"])]
    dirs = [(f, 1 if strength.set_index("factor").loc[f, "ic20"] > 0 else -1)
            for f in survivors]
    print(f"\n幸存者（三关全过）：{survivors}，方向 {dirs}")

    result = {"panel": f"{panel['date'].min().date()}~{panel['date'].max().date()}",
              "strength": strength.to_dict("records"),
              "survivors": survivors, "dirs": dirs}
    if not survivors:
        print("\n== 裁决：三关全灭——进攻型配方不建账户，进'已决策不做'清单 ==")
        result["verdict"] = "rejected"
        _save(result)
        return

    # ---- 组合关：冻结参数回测 vs corrected 同口径 ----
    print("\n== 组合关：50/20 冻结参数 + 生产成本（开盘执行/印花/滑点）==")
    prices, opens = load_price_matrix(
        sorted(panel["symbol"].unique()), start=START_DATE, adjust="qfq", store=store)
    idx = store.read("daily", "000300__none", columns=["date", "close"])
    bench = (idx.assign(date=pd.to_datetime(idx["date"])).set_index("date")["close"]
             .pct_change().reindex(prices.index))
    off = run_strategy(panel, dirs, prices, opens, bench, "进攻型配方")
    cur = run_strategy(panel, LIVE_FACTORS, prices, opens, bench, "现行 corrected")
    result["offensive"] = {k: v for k, v in off["metrics"].items()}
    result["corrected"] = {k: v for k, v in cur["metrics"].items()}
    result["verdict"] = "passed" if off["metrics"]["ir"] > 0 else "passed_negative_ir"
    _save(result)


def _save(result: dict) -> None:
    out = STORE_DIR / "research_results"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "offensive_gate.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
    print(f"\n已保存 {out / 'offensive_gate.json'}")
    try:
        record("offensive_gate",
               params={"recipe": OFFENSIVE, "horizon": HORIZON,
                       "top_n": TOP_N, "rebalance": REBAL, "start": START_DATE},
               metrics={r["factor"]: float(r["nw_t"]) for r in result["strength"]},
               notes=f"进攻型三关裁决：{result['verdict']}，幸存 {result['survivors']}",
               artifacts=["research_results/offensive_gate.json"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
