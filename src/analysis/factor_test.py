# -*- coding: utf-8 -*-
"""
因子有效性检验引擎（因子「分析」的核心）。

三板斧
------
1. IC / RankIC：每个交易日，因子值与「未来 N 日收益」在横截面上的相关性。
   - RankIC：因子 rank 与未来收益 rank 的 Pearson（抗极值，业界主流）。
   - 统计：IC 均值、ICIR=均值/标准差、IC 胜率(>0 占比)、t 值、样本月数。
   - 经验：|IC均值| > 0.03 有一定效果，> 0.05 较好；ICIR 绝对值 > 0.5 较稳。
2. 分层回测：每日按因子值分 Q 组（等权），看各组未来收益是否单调，
   以及「最高组 - 最低组」的多空收益。好因子应呈现清晰单调排列。
3. 衰减：对持有周期 1/5/10/20 日分别算 IC，观察预测力在几天后消失。

口径说明（D-038，修复报告2 P0-2/P0-3 两处口径裂缝）：
  1. fwd_h = close[t+1+h] / close[t+1] - 1 —— **T+1 才能成交**，窗口右移一天，
     剔除 t 收盘→t+1 开盘实盘拿不到的隔夜跳空（旧口径 close[t]→close[t+h] 高估）；
  2. PIT 面板有时间空洞（剔除期无行），shift 按行位置会无声跨洞——
     用全局交易日序号校验：future 与 base 必须恰好相隔 h 个交易日，否则置 NaN。
分层回测：建仓日（eff_date）只计日内段 close/open-1（开盘建仓拿不到隔夜），
merge_asof 加 tolerance（停牌/调出股不得沿用陈旧分组）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.analysis.factors import FACTOR_NAMES

TRADING_DAYS = 250


# ================================================================ 未来收益
def add_forward_returns(panel: pd.DataFrame,
                        horizons=(1, 5, 10, 20)) -> pd.DataFrame:
    """给面板追加 fwd_1/fwd_5/... **可成交**未来收益列（D-038）。

    窗口：T 日收盘出信号 -> T+1 收盘建仓（近似 T+1 开盘）-> 持有 h 日。
    空洞防护：PIT 过滤会在个股序列上打洞，按行 shift 会无声跨洞——
    用面板全局交易日序号校验 base/future 恰好相隔 h 个交易日，跨洞置 NaN。
    """
    out = panel.sort_values(["symbol", "date"]).copy()
    all_dates = pd.DatetimeIndex(np.sort(out["date"].unique()))
    tno_map = {d: i for i, d in enumerate(all_dates)}
    out["_tno"] = out["date"].map(tno_map)
    gpx = out.groupby("symbol")["close"]
    gtn = out.groupby("symbol")["_tno"]
    for h in horizons:
        base_px, base_t = gpx.shift(-1), gtn.shift(-1)
        fut_px, fut_t = gpx.shift(-(h + 1)), gtn.shift(-(h + 1))
        valid = (fut_t - base_t) == h
        out[f"fwd_{h}"] = (fut_px / base_px - 1.0).where(valid.fillna(False))
    return out.drop(columns=["_tno"]).sort_values(["date", "symbol"]).reset_index(drop=True)


# ================================================================ 截面相关（向量化）
def _sectional_pearson(data: pd.DataFrame, x: str, y: str,
                       min_n: int = 5) -> pd.Series:
    """按 date 分组，一次性用一/二阶矩计算截面 Pearson 相关。"""
    d = data[["date", x, y]].dropna().copy()
    if d.empty:
        return pd.Series(dtype=float)
    d["_xy"] = d[x] * d[y]
    d["_x2"] = d[x] ** 2
    d["_y2"] = d[y] ** 2
    s = d.groupby("date").agg(
        n=(x, "size"), mx=(x, "mean"), my=(y, "mean"),
        mxy=("_xy", "mean"), mx2=("_x2", "mean"), my2=("_y2", "mean"),
    )
    s = s[s["n"] >= min_n]
    cov = s["mxy"] - s["mx"] * s["my"]
    vx = (s["mx2"] - s["mx"] ** 2).clip(lower=0)
    vy = (s["my2"] - s["my"] ** 2).clip(lower=0)
    return cov / np.sqrt(vx * vy)


def rankic_series(panel: pd.DataFrame, factor: str, horizon: int) -> pd.Series:
    """RankIC 日序列：因子(已是rank) 与 未来收益截面rank 的 Pearson。"""
    y = f"fwd_{horizon}"
    d = panel[["date", factor, y]].dropna().copy()
    d[y] = d.groupby("date")[y].rank(pct=True)
    return _sectional_pearson(d, factor, y).dropna()


def _pearson(a: pd.Series, b: pd.Series) -> float:
    """等长序列 Pearson 相关（矩方法，纯向量化）。

    说明：个别 pandas/numpy 组合在 Windows 上调用 Series.corr / DataFrame.cov
    会原生崩溃（本仓库实测 pandas 3.0.6 + numpy 2.4.6 无声退出），
    矩方法不经过该代码路径，结果一致且无此问题。
    """
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    mask = a.notna() & b.notna()
    a, b = a[mask], b[mask]
    if len(a) < 2:
        return np.nan
    da, db = a - a.mean(), b - b.mean()
    va, vb = (da ** 2).mean(), (db ** 2).mean()
    if va <= 0 or vb <= 0:
        return np.nan
    return (da * db).mean() / np.sqrt(va * vb)


def _newey_west_tstat(x: pd.Series, max_lags: int) -> float:
    """Newey-West 自相关一致 t 值（Bartlett 核）。

    重叠持有期(h>1)会让相邻交易日的 IC 高度自相关，普通 t 值系统性虚高；
    max_lags 通常取 h-1。返回 NaN 表示样本不足或方差非正。
    """
    x = x.dropna()
    n = len(x)
    if n < 3 or max_lags < 1:
        return np.nan
    resid = x - x.mean()
    var = (resid ** 2).sum() / n
    for lag in range(1, min(max_lags, n - 1) + 1):
        w = 1 - lag / (max_lags + 1)      # Bartlett 权重
        var += 2 * w * (resid[lag:] * resid[:-lag]).sum() / n
    if var <= 0:
        return np.nan
    return x.mean() / np.sqrt(var / n)


# ================================================================ IC 汇总 / 衰减
def ic_summary(panel: pd.DataFrame,
               factors: list[str] | None = None,
               horizons=(1, 5, 10, 20)):
    """
    返回：
      table  长表 factor,horizon,n,ic_mean,ic_std,icir,pos_ratio,tstat,nw_tstat
             tstat 为普通 t 值；nw_tstat 为 Newey-West 自相关一致 t 值（重叠持有期下更可靠）
      series {(factor,horizon): RankIC 日序列}
    """
    factors = factors or FACTOR_NAMES
    rows, series = [], {}
    for f in factors:
        for h in horizons:
            # 严格口径：在「该因子与未来收益都非空」的当天样本内对未来收益排名
            # （不能在全样本预先排名，否则会改变 warmup 期的截面集合口径）
            s = rankic_series(panel, f, h)
            series[(f, h)] = s
            n = len(s)
            mean, std = s.mean(), s.std()
            rows.append({
                "factor": f, "horizon": h, "n": n,
                "ic_mean": mean, "ic_std": std,
                "icir": mean / std if std and std > 0 else np.nan,
                "pos_ratio": (s > 0).mean() if n else np.nan,
                "tstat": mean / (std / np.sqrt(n)) if std and std > 0 and n else np.nan,
                "nw_tstat": _newey_west_tstat(s, max(1, h - 1)) if n else np.nan,
            })
    return pd.DataFrame(rows), series


def ic_decay_wide(ic_table: pd.DataFrame, metric: str = "ic_mean") -> pd.DataFrame:
    """透视成 factor × horizon 的 IC 表，用于看衰减。"""
    return ic_table.pivot(index="factor", columns="horizon", values=metric)


# ================================================================ 绩效小工具
def _perf(r: pd.Series, nav: pd.Series) -> dict:
    n = len(r)
    total = nav.iloc[-1] - 1
    ann_ret = nav.iloc[-1] ** (TRADING_DAYS / n) - 1 if n else np.nan
    vol = r.std() * np.sqrt(TRADING_DAYS)
    sharpe = r.mean() * TRADING_DAYS / vol if r.std() > 0 else np.nan
    mdd = (nav / nav.cummax() - 1).min()
    return {
        "total": total, "ann_ret": ann_ret, "ann_vol": vol,
        "sharpe": sharpe, "mdd": mdd, "win": (r > 0).mean(),
    }


# ================================================================ 分层回测
def quantile_backtest(panel: pd.DataFrame, factor: str,
                      n_groups: int = 5, rebalance: int = 20,
                      fee: float = 0.00025, slippage: float = 0.0005) -> dict:
    """
    定期（每 rebalance 个交易日）按因子分组、等权持有至下期，再平衡时按实际
    换手扣 fee+slippage。避免「每日全量再平衡且零成本」造成的净值幻觉。

    分组在再平衡日（因子当日收盘已知），下一交易日生效；持有期成分固定。
    口径（D-038）：建仓日只计日内段 close/open-1（开盘建仓拿不到隔夜）；
    merge_asof 带 tolerance——停牌/调出股在空洞后不得沿用陈旧分组。
    收益为 close 口径。返回 daily(扣费后各组日收益)、nav、ls/nav_ls、stats、单调性。
    """
    d = panel[["date", "symbol", factor, "ret"]].dropna().copy()
    has_open = "open" in panel.columns
    if has_open:
        d["open"] = panel.loc[d.index, "open"]
    d["date"] = pd.to_datetime(d["date"])
    dates = np.sort(d["date"].unique())
    reb_dates = dates[::rebalance]
    cost_rate = fee + slippage

    # 再平衡日分组快照
    snap = d[d["date"].isin(reb_dates)][["date", "symbol", factor]].copy()
    snap["grp"] = snap.groupby("date")[factor].transform(
        lambda s: np.ceil(s.rank(pct=True) * n_groups)).astype(int)

    # 生效日 = 再平衡日的下一个交易日
    didx = pd.DatetimeIndex(dates)
    # R3-16：末次再平衡若 +1 越界则丢弃该期（钳位=吃到信号前隔夜，1 天前视）
    snap = snap[snap["date"].map(lambda rd: didx.get_loc(rd) + 1 < len(dates))].copy()
    snap["eff_date"] = snap["date"].map(lambda rd: dates[didx.get_loc(rd) + 1])
    reb = snap[["eff_date", "symbol", "grp"]].rename(
        columns={"eff_date": "date"}).sort_values(["date", "symbol"])

    # merge_asof：每个交易日映射到最近一次生效分组（持有期成分固定）
    # tolerance：距上次生效超过 3 个调仓周期视为陈旧（停牌/调出股，不得沿用）
    m = pd.merge_asof(
        d.sort_values(["date", "symbol"]), reb,
        by="symbol", on="date", direction="backward",
        tolerance=pd.Timedelta(days=rebalance * 3 + 10))
    m = m.dropna(subset=["grp"]).copy()
    m["grp"] = "Q" + m["grp"].astype(int).astype(str)

    # 建仓日只计日内段（开盘建仓拿不到隔夜跳空，D-038）
    if has_open:
        if "close" not in m.columns:
            m = m.merge(panel[["date", "symbol", "close"]], on=["date", "symbol"],
                        how="left")
        eff = m["date"].isin(set(snap["eff_date"]))
        m.loc[eff, "ret"] = (m.loc[eff, "close"] / m.loc[eff, "open"] - 1.0).fillna(0.0)

    grouped = m.groupby(["date", "grp"])["ret"]
    daily = grouped.mean().unstack("grp").sort_index()
    daily_med = grouped.median().unstack("grp").sort_index()
    qcols = sorted(daily.columns)
    daily = daily.reindex(columns=qcols).fillna(0.0)
    daily_med = daily_med.reindex(columns=qcols).fillna(0.0)
    hi_col, lo_col = qcols[-1], qcols[0]

    # 再平衡换手成本（按组，实际成分权重变化）；批量收集后一次 pivot，避免逐格 .loc
    snap_by_eff = {ed: g for ed, g in snap.groupby("eff_date")}
    prev_w: dict[str, dict] = {}
    cost_rows = []
    for ed, cur in snap_by_eff.items():
        members_by_g = {f"Q{gid}": set(gdf["symbol"])
                        for gid, gdf in cur.groupby("grp")}
        for g in qcols:
            members = members_by_g.get(g, set())
            w = 1.0 / len(members) if members else 0.0
            old = prev_w.get(g, {})
            turn = sum(abs((w if x in members else 0.0) - old.get(x, 0.0))
                       for x in members | set(old))
            cost_rows.append((ed, g, turn * cost_rate))
            prev_w[g] = {x: w for x in members}
    cost_long = pd.DataFrame(cost_rows, columns=["date", "grp", "cost"])
    cost = (cost_long.pivot(index="date", columns="grp", values="cost")
            .reindex(index=daily.index, columns=qcols).fillna(0.0))

    daily_net = daily - cost
    nav = (1 + daily_net).cumprod()
    ls = daily_net[hi_col] - daily_net[lo_col]
    nav_ls = (1 + ls).cumprod()

    # 稳健口径：组内个股日收益「中位数」，不被肥尾暴涨拉动
    daily_med_net = daily_med - cost
    nav_med = (1 + daily_med_net).cumprod()
    ls_med = daily_med_net[hi_col] - daily_med_net[lo_col]
    nav_ls_med = (1 + ls_med).cumprod()

    stats = pd.DataFrame(
        {c: _perf(daily_net[c], nav[c]) for c in qcols}).T

    totals = stats["total"].reset_index(drop=True)
    grp_idx = pd.Series(range(1, len(totals) + 1), dtype=float)
    if len(totals) >= 2:
        mono_corr = _pearson(grp_idx, totals.rank().astype(float))
        strict = bool(totals.is_monotonic_increasing
                      or totals.is_monotonic_decreasing)
    else:
        mono_corr, strict = np.nan, False

    return {
        "factor": factor, "daily": daily_net, "nav": nav,
        "ls_ret": ls, "nav_ls": nav_ls,
        "daily_median": daily_med_net, "nav_median": nav_med,
        "ls_median": ls_med, "nav_ls_median": nav_ls_med,
        "stats": stats,
        "mono_corr": mono_corr, "strict_monotonic": strict,
        "hi_col": hi_col, "lo_col": lo_col, "turnover_cost": cost,
    }


# ================================================================ 一键检验
def run_factor_tests(panel: pd.DataFrame,
                     factors: list[str] | None = None,
                     horizons=(1, 5, 10, 20),
                     n_groups: int = 5) -> dict:
    """追加未来收益 → IC 汇总 → 各因子分层回测，返回完整结果。"""
    factors = factors or FACTOR_NAMES
    p = add_forward_returns(panel, horizons)
    ic_tbl, ic_sers = ic_summary(p, factors, horizons)
    quant = {f: quantile_backtest(p, f, n_groups) for f in factors}
    return {
        "panel": p, "ic_table": ic_tbl,
        "ic_series": ic_sers, "quantile": quant,
    }
