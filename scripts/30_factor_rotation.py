"""第 30 步：全因扒手——因子轮动研究（D-061，用户 2026-11-07 提出）。

理念（用户原话 → 规则化）：
  将当前所拥有因子进行排列组合，将 T-2 日优势因子组合为 T-1 做策略，
  T-1 仍旧优势的因子保留；其他因子有上涨动向的，拿基准线看配不配进入；
  T 日按因子排序交易。
学术对应：因子动量 / 因子轮动（Gupta & Kelly 2019, Factor Momentum Everywhere）
—— 因子自身的有效性有时序动量，动态选择/加权有机会跑赢固定因子组合。
本项目此前结论支持这个假设的动机：R3-12 显示 46 个表达式因子 BH-FDR 后无一
显著（固定全样本筛选下不了手），而 dy_ttm 分年 IC 有明显风格周期（D-027）——
"谁有效用谁"正是对这两个问题的另一种回答。

规则（每个调仓日 t，每 rebalance 个交易日）：
  1) 对因子池每个因子 f 计算日频 RankIC 序列（D-038 可成交口径：
     fwd_h = close[t+1+h]/close[t+1]-1，剔隔夜）；
  2) 全窗口优势  ic_full  = mean(IC[t-window-h : t-h])   （严格滞后，无前视）
     近端动量    ic_recent = mean(IC[t-20-h : t-h])
  3) 选因子（三种模式）：
     · topk    ：|ic_full| 最大的 K 个，方向 = sign(ic_full)；
     · thresh  ：|ic_full| ≥ θ 的全部（至多 8 个，按 |ic_full| 截断）——"基准线"；
     · momentum：thresh 集合 ∪ {ic_recent 越过准入线 θ_e 且与 ic_full 同号的因子}
                 ——用户说的"有上涨动向、配不配进"。
     选不出（thresh/momentum 空集）→ 回退 corrected 三因子。
  4) 合成：入选因子方向对齐后截面 rank 等权均值 → Top-N 等权 → 组合引擎
     T+1 开盘执行（与 05 完全同口径：最低佣金/印花/过户/滑点/涨跌停停牌跳过）。

防过拟合：
  · 变体×参数网格一次全报告（不看单一最优），参数敏感性本身就是结论；
  · 全周期 + 样本外（2022+）双窗口；对照 corrected/dividend/topkdrop 三基线
    （同一面板、同一价格矩阵、同一引擎）。
  · 注意：网格最优 = 样本内择优，只是"初筛"；入选策略须过 06 walk-forward
    与 28 随机对照才能进影子盘（与 D-031 表达式因子同纪律）。

用法：
    python scripts/30_factor_rotation.py            # 面板缓存存在则直接用
    python scripts/30_factor_rotation.py --rebuild  # 强制重建面板缓存
"""
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis import expr_factors as EF
from src.analysis.factors import build_factor_panel
from src.backtest.portfolio import portfolio_performance, run_portfolio_backtest
from src.data import financials, universe
from src.data.market import get_kline, load_price_matrix
from src.data.storage import ParquetStore
from src.strategies.multi_factor import (
    STRATEGY_FACTORS,
    composite_topn,
    topk_dropout_topn,
)

START_DATE = "20180101"
OOS_START = "2022-01-01"
TOP_N = 50
REBALANCE = 20
PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "research_results"
FALLBACK = STRATEGY_FACTORS["corrected"]  # 选不出因子时的回退（现行 corrected）


# ---------------------------------------------------------------- 面板（带缓存）
def load_or_build_panel(store: ParquetStore, force: bool = False) -> pd.DataFrame:
    """全量因子面板：8 内置 + dy_ttm + 46 表达式，PIT 过滤，截面 rank。结果缓存。"""
    if PANEL_CACHE.exists() and not force:
        panel = pd.read_parquet(PANEL_CACHE)
        # 统一 ns 精度：parquet 读回可能是 us 单位，与 load_price_matrix 的 ns 索引混用有坑
        panel["date"] = pd.to_datetime(panel["date"]).astype("datetime64[ns]")
        print(f">> 面板缓存命中：{PANEL_CACHE.name}（{len(panel)} 行，"
              f"{panel['date'].min().date()}~{panel['date'].max().date()}）")
        return panel
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    try:
        div_panel = financials.load_dividend_panel(store, symbols)
    except Exception as e:  # noqa: BLE001
        print(f"   [warn] 分红面板加载失败（dy_ttm 缺席）: {e}")
        div_panel = None
    exprs = {k: EF.EXPR_FACTORS[k]["expr"] for k in EF.EXPR_FACTORS}  # 全部 46（EXPR_NAMES 定型于批次2合入前，只有 32）
    print(f">> 构建 PIT 因子面板（{len(symbols)} 只历史成分 × "
          f"{9 + len(exprs)} 因子）...", flush=True)
    panel = build_factor_panel(store, symbols, start=START_DATE,
                               pit_constituents=pit, dividend_panel=div_panel,
                               expr_factors=exprs)
    rank_cols = [c for c in panel.columns
                 if c not in ("date", "symbol", "open", "close", "ret")]
    panel[rank_cols] = panel[rank_cols].astype("float32")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(PANEL_CACHE, index=False)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{len(panel)} 行 → 缓存 {PANEL_CACHE.name}")
    return panel


# ---------------------------------------------------------------- 日频 IC 序列
def _wide_fwd(px: pd.DataFrame, h: int) -> pd.DataFrame:
    """D-038 可成交口径前瞻收益：T 收盘出信号，T+1 建仓持有 h 日（剔隔夜跳空）。"""
    return px.shift(-(h + 1)) / px.shift(-1) - 1.0


def daily_ic(panel: pd.DataFrame, factor: str, fwd: pd.DataFrame) -> pd.Series:
    """因子 f 的日频 RankIC（向量化：逐日截面 Pearson，rank 已在面板中完成）。

    配对口径：只统计 (因子, 前瞻收益) **同时非缺失**的格子——停牌/因子缺 warmup
    的标的必须两边同时剔除，否则分子分母的求和集合不一致，variance 项可为负
    （实测 331 天 den=NaN 的根源）。
    """
    x = panel.pivot(index="date", columns="symbol", values=factor).astype("float64")
    x = x.reindex(columns=fwd.columns)
    y = fwd.reindex_like(x)
    mask = x.notna() & y.notna()
    n = mask.sum(axis=1).astype("float64")
    xm = x.where(mask)
    ym = y.where(mask)
    sx, sy = xm.sum(axis=1), ym.sum(axis=1)
    sxy = (xm * ym).sum(axis=1)
    sxx = (xm * xm).sum(axis=1)
    syy = (ym * ym).sum(axis=1)
    num = n * sxy - sx * sy
    with np.errstate(invalid="ignore"):
        den = np.sqrt((n * sxx - sx * sx) * (n * syy - sy * sy))
    ic = (num / den).where((n >= 30) & (den > 0))
    return ic


def _pearson_pair(a: np.ndarray, b: np.ndarray) -> float:
    """两个等长序列的 Pearson 相关（手写，绕开 pandas .corr 的进程崩溃）。"""
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else np.nan


# ---------------------------------------------------------------- 轮动选因子
def rotation_weights(
    panel: pd.DataFrame,
    ic_stats: dict[tuple[str, int], pd.Series],
    factors: list[str],
    px_dates: pd.DatetimeIndex,
    mode: str = "topk",
    window: int = 120,
    ic_h: int = 5,
    top_k: int = 3,
    theta: float = 0.02,
    theta_entry: float = 0.015,
    top_n: int = TOP_N,
    rebalance_days: int = REBALANCE,
) -> tuple[pd.DataFrame, list[dict]]:
    """因子轮动目标权重矩阵。返回 (W, 每期选因子日志)。

    滞后保证：t 日的选择只用 IC[t-window-h : t-h]——ic 序列在第 d 天的值
    本身依赖 d 之后 h 天的收益，再滞后 h 天才可被 t 日信号使用（"T-2 为 T-1 做策略"）。
    """
    # 预载调仓日会用到的截面 rank 宽表（因子 × 日期），按需构建
    rank_wide: dict[str, pd.DataFrame] = {}
    dates = px_dates[::max(1, rebalance_days)]
    rows: dict = {}
    log: list[dict] = []
    corrected = [f for f, _ in FALLBACK]
    for t in dates:
        # 该日截面（因子 rank 已是 0~1 百分位）
        day = panel[panel["date"] == t]
        if day.empty:
            continue
        # 各因子在 t 日的可选统计量（ic 序列 shifted-h 后，t 位置即 ic_full/ic_recent）
        stats = {}
        for f in factors:
            s = ic_stats.get((f, ic_h))
            if s is None:
                continue
            seg = s.reindex(px_dates)
            pos = px_dates.get_loc(t)
            # IC(d) 用到 close[d+h+1]：t 日已知要求 d+h+1 <= t，即 d <= t-h-1
            hi = pos - ic_h - 1
            if hi < 20:
                continue
            lo = max(0, hi - window)
            seg_win = seg.iloc[lo:hi + 1].dropna()
            if len(seg_win) < window / 2:
                continue
            ic_full = float(seg_win.mean())
            rec_lo = max(0, hi - 20)
            ic_recent = float(seg.iloc[rec_lo:hi + 1].dropna().mean())
            stats[f] = (ic_full, ic_recent)
        if not stats:
            sel, logrow = [(f, -1) for f in corrected], {"date": t, "sel": corrected,
                                                         "fallback": True}
        else:
            if mode == "topk":
                ranked = sorted(stats.items(), key=lambda kv: abs(kv[1][0]),
                                reverse=True)[:top_k]
                sel = [(f, (1 if v[0] > 0 else -1)) for f, v in ranked]
            elif mode == "thresh":
                passed = [(f, v[0]) for f, v in stats.items() if abs(v[0]) >= theta]
                passed.sort(key=lambda kv: abs(kv[1]), reverse=True)
                sel = [(f, (1 if v > 0 else -1)) for f, v in passed[:8]]
            elif mode == "momentum":
                passed = [(f, v[0]) for f, v in stats.items() if abs(v[0]) >= theta]
                passed.sort(key=lambda kv: abs(kv[1]), reverse=True)
                cand = [(f, 1 if v > 0 else -1, abs(v)) for f, v in passed[:8]]
                for f, (icf, icr) in stats.items():
                    # "上涨动向"准入：近端 IC 越过准入线、且与全窗口方向一致
                    if (all(f != c[0] for c in cand) and abs(icr) >= theta_entry
                            and np.sign(icr) == np.sign(icf if icf != 0 else icr)):
                        cand.append((f, 1 if icr > 0 else -1, abs(icr)))
                cand.sort(key=lambda c: c[2], reverse=True)
                sel = [(f, d) for f, d, _ in cand[:8]]  # 总量封顶，防满池均值化
            else:
                raise ValueError(f"未知 mode: {mode}")
            if not sel:
                sel, logrow = [(f, -1) for f in corrected], {
                    "date": t, "sel": corrected, "fallback": True}
            else:
                logrow = {"date": t, "sel": [f for f, _ in sel], "fallback": False}
        # 合成分：方向对齐 rank 等权均值 → Top-N
        score = None
        for f, d in sel:
            if f not in rank_wide:
                rank_wide[f] = panel.pivot(index="date", columns="symbol",
                                           values=f).astype("float64")
            r = rank_wide[f].loc[t] if t in rank_wide[f].index else None
            if r is None:
                continue
            r = r * d if d > 0 else (1.0 - r)
            score = r if score is None else score + r
        if score is None or score.notna().sum() == 0:
            continue
        score = (score / len(sel)).dropna()
        top = score.nlargest(top_n)
        rows[pd.Timestamp(t)] = pd.Series(1.0 / len(top), index=top.index.astype(str))
        logrow["n"] = len(sel)
        log.append(logrow)
    W = pd.DataFrame(rows).T.fillna(0.0)
    W.index.name = "date"
    return W, log


# ---------------------------------------------------------------- 引擎封装
def run_one(tag: str, W: pd.DataFrame, prices: pd.DataFrame,
            opens: pd.DataFrame, bench_ret: pd.Series | None,
            t0: pd.Timestamp) -> dict:
    """跑一个策略：全周期（自 t0 起）+ OOS（2022+ 切片统计）。返回摘要行。"""
    Wx = W[W.index >= t0]
    res = run_portfolio_backtest(
        Wx, prices, fee=0.00025, min_fee=5.0, slippage=0.0005,
        stamp_duty=0.0005, transfer_fee=0.00001, limit_skip=True,
        init_capital=1_000_000.0,
        benchmark_returns=bench_ret, open_prices=opens)
    perf = portfolio_performance(res)
    row = {
        "策略": tag,
        "起算日": str(t0.date()),
        "总收益": perf["总收益率"], "年化": perf["年化收益率"],
        "MDD": perf["最大回撤"], "Calmar": perf["Calmar比率"],
        "夏普": perf["夏普比率"], "年换手": perf["年均换手率"],
        "成本": perf.get("成本合计", np.nan),
        "超额IR": perf.get("信息比率", np.nan),
    }
    # OOS 切片统计（净值路径一致，只重算统计）
    nav = res["nav"]
    ret = res["daily_ret"]
    oos = nav.index >= pd.Timestamp(OOS_START)
    if oos.any():
        n2 = res["nav"][oos]
        r2 = ret[oos]
        ann2 = float(n2.iloc[-1] / n2.iloc[0]) ** (250 / max(len(n2), 1)) - 1
        dd2 = float((n2 / n2.cummax() - 1).min())
        row["OOS总收益"] = float(n2.iloc[-1] / n2.iloc[0] - 1)
        row["OOS年化"] = ann2
        row["OOS_MDD"] = dd2
        row["OOS_Calmar"] = ann2 / abs(dd2) if dd2 < 0 else np.nan
        if bench_ret is not None:
            exc = r2 - bench_ret.reindex(r2.index).fillna(0.0)
            te = float(exc.std() * np.sqrt(250))
            row["OOS超额IR"] = float(exc.mean() * 250 / te) if te > 0 else np.nan
    else:
        for c in ("OOS总收益", "OOS年化", "OOS_MDD", "OOS_Calmar", "OOS超额IR"):
            row[c] = np.nan
    return row, res


def _fmt(v, pct=True):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v:+.1%}" if pct else f"{v:.2f}"


def main() -> None:
    force = "--rebuild" in sys.argv
    store = ParquetStore()
    panel = load_or_build_panel(store, force=force)
    factors = [c for c in panel.columns
               if c not in ("date", "symbol", "open", "close", "ret")]
    print(f">> 因子池 {len(factors)} 个：{factors}")

    # 价格矩阵（全历史口径，D-050）与基准
    pit_syms = universe.pit_union_symbols("000300", store)
    prices, opens = load_price_matrix(pit_syms, start=START_DATE, store=store)
    panel_dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel["date"]).unique()))
    prices = prices.reindex(panel_dates)
    opens = opens.reindex(panel_dates)
    b = get_kline("000300", adjust="none", store=store).set_index("date")["close"]
    bench_ret = b.pct_change()

    # 日频 IC（每因子 × {5, 20}）——轮动信号的一次性原料
    print(">> 计算日频 RankIC 序列（55 因子 × 持有 5/20 日）...", flush=True)
    fwd = {h: _wide_fwd(prices, h) for h in (5, 20)}
    ic_stats: dict[tuple[str, int], pd.Series] = {}
    for f in factors:
        for h, fw in fwd.items():
            ic_stats[(f, h)] = daily_ic(panel, f, fw)
    print(f"   完成 {len(ic_stats)} 条 IC 序列")

    # 机理诊断：这个因子池里到底有没有"因子动量"可收割？
    # 度量：corr(截至 t 的 120 日滚动 IC 均值, t 之后 60 日 IC 均值)——
    # 若 ≈0/负，"谁最近有效就用谁"没有信息来源，轮动必然退化为噪声择时。
    # min_periods 放宽：IC 序列有零散 NaN（停牌/退市日），窗口内有足够有效值即可。
    mom_diag = {}
    for f in factors:
        try:
            for h in (5, 20):
                s = ic_stats[(f, h)]
                past = s.rolling(120, min_periods=60).mean()
                fut = s.shift(-60).rolling(60, min_periods=30).mean()
                pair = pd.concat([past, fut], axis=1, keys=["p", "f"]).dropna()
                if len(pair) > 100:
                    mom_diag[f] = _pearson_pair(pair["p"].to_numpy(float),
                                                pair["f"].to_numpy(float))
        except Exception as e:  # noqa: BLE001  单因子诊断失败不拖垮研究
            print(f"   [diag] {f} 失败: {e}")
    md = pd.Series(mom_diag)
    print(f"   [诊断] 因子动量强度 corr(过去120日IC, 未来60日IC)："
          f"均值 {md.mean():+.3f}，中位数 {md.median():+.3f}，"
          f"|r|>0.1 的因子 {int((md.abs() > 0.1).sum())}/{len(md)} 个，"
          f"最大 {md.abs().idxmax()}({md.max():+.3f})")

    # 基线（同一面板/价格/引擎）：corrected、dividend、topkdrop
    print(">> 生成基线权重矩阵...", flush=True)
    W_base = {
        "corrected(基线)": composite_topn(panel, STRATEGY_FACTORS["corrected"],
                                          top_n=TOP_N, rebalance_days=REBALANCE),
        "dividend(基线)": composite_topn(panel, STRATEGY_FACTORS["dividend"],
                                         top_n=TOP_N, rebalance_days=REBALANCE),
        "topkdrop(基线)": topk_dropout_topn(
            panel, STRATEGY_FACTORS["topkdrop"], topk=TOP_N, n_drop=5,
            rebalance_days=REBALANCE),
    }

    # 全因扒手变体（用户理念的三种规则化 + 敏感性网格）
    variants = {
        "扒手-topK3/w120/h5": dict(mode="topk", window=120, ic_h=5, top_k=3),
        "扒手-topK5/w120/h5": dict(mode="topk", window=120, ic_h=5, top_k=5),
        "扒手-topK3/w60/h5":  dict(mode="topk", window=60, ic_h=5, top_k=3),
        "扒手-topK3/w120/h20": dict(mode="topk", window=120, ic_h=20, top_k=3),
        "扒手-阈值.02/w120/h5": dict(mode="thresh", window=120, ic_h=5, theta=0.02),
        "扒手-阈值.03/w120/h5": dict(mode="thresh", window=120, ic_h=5, theta=0.03),
        "扒手-动量准入/w120/h5": dict(mode="momentum", window=120, ic_h=5,
                                      theta=0.03, theta_entry=0.015),
    }
    W_rot: dict[str, pd.DataFrame] = {}
    logs: dict[str, list[dict]] = {}
    for tag, cfg in variants.items():
        W, log = rotation_weights(panel, ic_stats, factors, panel_dates, **cfg)
        W_rot[tag] = W
        logs[tag] = log
        fb = sum(1 for r in log if r.get("fallback"))
        print(f"   {tag}: 信号 {len(W)} 期，回退 {fb} 期", flush=True)

    # 起算日统一 = 最早轮动信号日（此后各策略同一窗口对比，避免起点不一）
    t0 = min(W.index.min() for W in W_rot.values())
    print(f">> 统一起算日 {t0.date()}（轮动首批信号需要 {variants['扒手-topK3/w120/h5']['window']} 日回看）")

    rows_out = []
    navs = {}
    for tag, W in {**W_base, **W_rot}.items():
        row, res = run_one(tag, W, prices, opens, bench_ret, t0)
        rows_out.append(row)
        navs[tag] = res["nav"]
        print(f"   [done] {tag}", flush=True)

    summary = pd.DataFrame(rows_out)
    cols = ["策略", "总收益", "年化", "MDD", "Calmar", "夏普", "年换手", "成本",
            "超额IR", "OOS总收益", "OOS年化", "OOS_MDD", "OOS_Calmar", "OOS超额IR"]
    summary = summary[cols].sort_values("OOS年化", ascending=False)
    print()
    print("== 全因扒手 vs 基线（保真口径，统一自 t0 起算）==")
    for _, r in summary.iterrows():
        print(f"  {r['策略']:<22} 全期 {_fmt(r['总收益'])} 年化 {_fmt(r['年化'])} "
              f"MDD {_fmt(r['MDD'])} Calmar {r['Calmar']:.2f} 换手 {r['年换手']:.0%} "
              f"| OOS {_fmt(r['OOS总收益'])} 年化 {_fmt(r['OOS年化'])} "
              f"MDD {_fmt(r['OOS_MDD'])} Calmar {r['OOS_Calmar']:.2f} "
              f"IR {r['OOS超额IR']:.2f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(OUT_DIR / "factor_rotation_summary.parquet", index=False)
    for tag in list(W_rot)[:3]:  # 只落前三个变体的净值与选因子日志
        safe = tag.replace("/", "_").replace(".", "")
        navs[tag].rename("nav").to_frame().to_parquet(
            OUT_DIR / f"rotation_nav_{safe}.parquet")
        pd.DataFrame(logs[tag]).to_parquet(OUT_DIR / f"rotation_sel_{safe}.parquet",
                                           index=False)

    # 每期选因子日志透视：哪些因子最常被轮动选中（理解轮动在做什么）
    allsel = pd.DataFrame([{"date": r["date"], "factor": f}
                           for lg in logs.values() for r in lg for f in r["sel"]])
    if len(allsel):
        freq = allsel["factor"].value_counts().head(15)
        print("\n== 轮动最常选中的因子 Top15 ==")
        for f, n in freq.items():
            print(f"  {f:<12} {n} 期")

    try:
        from src.analysis.experiments import record
        best = summary.iloc[0]
        record("factor_rotation",
               params={"pool": len(factors), "variants": len(variants),
                       "t0": str(t0.date())},
               metrics={"best_oos_ann": float(best["OOS年化"]),
                        "best_oos_mdd": float(best["OOS_MDD"])},
               notes="全因扒手首轮研究：7 变体全报告，结论见 DECISIONS D-061",
               artifacts=["research_results/factor_rotation_summary.parquet"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] 记录失败: {e}")


if __name__ == "__main__":
    main()
