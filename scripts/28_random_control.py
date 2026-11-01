"""
随机对照实验（D-049）：在同一套股票池、调仓日、成本、成交口径下，
用随机选股替换策略选股，看策略的超额到底是不是"随便选也能做到"。

做法：
  1. 读 PIT 成分 + 日线（只取 open/close），构造与 05 完全一致的价格矩阵；
  2. 用策略自己的权重矩阵跑一遍引擎，核对能否复现 05 存的净值（校验台架）；
  3. 随机对照 A：每期完全重抽 50 只（换手比策略高，成本更差）；
     随机对照 B：每期只换掉与策略相同数量（按策略真实换手匹配，公平口径）；
  4. 输出：策略在随机分布里的百分位、分位数表、以及每档的胜率。

用法：
    python scripts/28_random_control.py                 # 默认 200 次
    python scripts/28_random_control.py 500             # 500 次
"""
import json
import sys

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.backtest.portfolio import run_portfolio_backtest
from src.data import universe
from src.data.storage import ParquetStore

START = "2018-01-01"
TOP_N = 50
REB = 20
COST = dict(fee=0.00025, min_fee=5.0, slippage=0.0005,
            stamp_duty=0.0005, transfer_fee=0.00001,
            limit_skip=True, init_capital=1_000_000.0)
WINDOWS = {"2019+": "2019-01-02", "2022+": "2022-01-04"}
OUT_DIR = STORE_DIR / "research_results"


def load_prices(store: ParquetStore):
    """价格矩阵用**全部历史**（不做 PIT 删行），PIT 只用来生成"当天能不能买"的资格掩码。

    为什么：早先的实现先按成分股删行，非成分期间价格变成 NaN，引擎 ffill 补洞，
    等股票重回指数时把几年涨跌压成一天（2021-12-10 / 2022-06-10 等调样日批量出现
    +300% 的假收益），任何按"随机组合"做的对照都会被这种假跳变污染。
    """
    pit = universe.load_pit_constituents("000300", store)
    symbols = universe.pit_union_symbols("000300", store)
    file_of = {n.split("__")[0]: n for n in store.list_names("daily") if n.endswith("__qfq")}
    frames = []
    for s in symbols:
        name = file_of.get(str(s))
        if name is None:
            continue
        try:
            df = store.read("daily", name, columns=["date", "symbol", "open", "close"])
        except Exception:  # noqa: BLE001
            continue
        if df is None or df.empty:
            continue
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel[panel["date"] >= pd.Timestamp(START)]
    prices = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    opens = panel.pivot(index="date", columns="symbol", values="open").sort_index()

    # 资格掩码：当天确实是沪深300成分才允许买入；估值/收益一律用真实价格
    p = pit[["symbol", "in_date", "out_date"]].copy()
    p["in_date"] = pd.to_datetime(p["in_date"])
    p["out_date"] = pd.to_datetime(p["out_date"])
    idx = prices.index
    elig = pd.DataFrame(False, index=idx, columns=prices.columns)
    for s, g in p.groupby("symbol"):
        s = str(s)
        if s not in elig.columns:
            continue
        for _, r in g.iterrows():
            mask = idx >= r["in_date"]
            if pd.notna(r["out_date"]):
                mask &= idx < r["out_date"]
            elig.loc[mask, s] = True
    return prices, opens, elig


def pool_on(day, prices, elig):
    """当天可买且当天有价格的标的。"""
    px = prices.loc[day]
    return elig.loc[day][elig.loc[day] & px.notna()].index


def window_return(nav: pd.Series, start: str) -> float:
    seg = nav[nav.index >= pd.Timestamp(start)]
    if len(seg) < 2:
        return float("nan")
    return float(seg.iloc[-1] / seg.iloc[0] - 1.0)


def run(W, prices, opens) -> pd.Series:
    res = run_portfolio_backtest(W, prices, **COST, open_prices=opens)
    return res["nav"]


def main() -> None:
    draws = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    store = ParquetStore()
    print(">> 载入 PIT 面板（只取价格）...", flush=True)
    prices, opens, elig = load_prices(store)
    dates = prices.index
    print(f"   面板 {dates[0].date()} ~ {dates[-1].date()}，{prices.shape[1]} 只，{len(dates)} 交易日",
          flush=True)

    # 策略自己的权重（05 产物）——用同一引擎复跑，做台架校验
    w_path = STORE_DIR / "portfolio_results" / f"weights_000300_top{TOP_N}_reb{REB}.parquet"
    W_strat = pd.read_parquet(w_path)
    W_strat.index = pd.to_datetime(W_strat.index)
    W_strat = W_strat.reindex(columns=prices.columns).fillna(0.0)
    nav_strat = run(W_strat, prices, opens)

    stored = pd.read_parquet(
        STORE_DIR / "portfolio_results" / f"nav_000300_top{TOP_N}_reb{REB}.parquet")
    if "date" in stored.columns:
        stored = stored.set_index("date")
    stored = stored["nav"].astype(float)
    stored.index = pd.to_datetime(stored.index)
    common = nav_strat.index.intersection(stored.index)
    rel = float((nav_strat.reindex(common) / stored.reindex(common) - 1.0).abs().max())
    print(f"   台架校验：用权重重跑 vs 05 存档净值，最大相对偏差 {rel:.2e}", flush=True)

    reb_dates = [d for d in W_strat.index if d in prices.index]
    # 策略每期真实换手（买卖各算一半）：用于口径 B 的匹配
    Wb = (W_strat > 0)
    turn = (Wb.diff().abs().sum(axis=1) / 2.0).dropna()
    k_rep = int(round(float(turn.mean())) if len(turn) else TOP_N // 2)
    k_rep = max(1, min(TOP_N, k_rep))
    print(f"   策略每期平均换出 {float(turn.mean()):.1f} 只（共 {TOP_N} 只）→ 口径 B 每次随机换 {k_rep} 只",
          flush=True)

    rng = np.random.default_rng(20261029)
    res_a = []
    print(f">> 开始 {draws} 次随机对照...", flush=True)
    for i in range(draws):
        # A：每期完全重抽
        rows = {}
        for d in reb_dates:
            pool = pool_on(d, prices, elig)
            if len(pool) < TOP_N:
                continue
            pick = rng.choice(pool.to_numpy(), TOP_N, replace=False)
            rows[d] = pd.Series(1.0 / TOP_N, index=pick)
        Wa = pd.DataFrame(rows).T.reindex(columns=prices.columns).fillna(0.0)
        Wa.index = pd.to_datetime(Wa.index)
        nav_a = run(Wa, prices, opens)

        # B：从上一期持仓里保留 (TOP_N - k_rep)，其余随机补
        rows = {}
        held: list = []
        for d in reb_dates:
            pool = pool_on(d, prices, elig)
            if len(pool) < TOP_N:
                continue
            keep = [s for s in held if s in pool][: max(0, TOP_N - k_rep)]
            rest = [s for s in pool if s not in keep]
            need = TOP_N - len(keep)
            add = list(rng.choice(np.array(rest, dtype=object), need, replace=False)) if need > 0 else []
            held = keep + [str(x) for x in add]
            rows[d] = pd.Series(1.0 / TOP_N, index=held)
        Wb2 = pd.DataFrame(rows).T.reindex(columns=prices.columns).fillna(0.0)
        Wb2.index = pd.to_datetime(Wb2.index)
        nav_b = run(Wb2, prices, opens)

        rec = {"draw": i}
        for label, st in WINDOWS.items():
            rec[f"strat_{label}"] = window_return(nav_strat, st)
            rec[f"A_{label}"] = window_return(nav_a, st)
            rec[f"B_{label}"] = window_return(nav_b, st)
        res_a.append(rec)
        if (i + 1) % 20 == 0:
            print(f"   进度 {i + 1}/{draws}", flush=True)

    df = pd.DataFrame(res_a)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / f"random_control_000300_top{TOP_N}_reb{REB}.parquet", index=False)

    summary = {}
    print("\n== 随机对照结果 ==", flush=True)
    for label in WINDOWS:
        st = window_return(nav_strat, WINDOWS[label])
        for tag, col in (("A 完全重抽", f"A_{label}"), ("B 匹配换手", f"B_{label}")):
            arr = df[col].to_numpy(dtype=float)
            pct = float((arr < st).mean() * 100)
            summary[f"{tag}_{label}"] = {
                "median": float(np.median(arr)), "p10": float(np.percentile(arr, 10)),
                "p90": float(np.percentile(arr, 90)), "max": float(arr.max()),
                "strategy": st, "strategy_percentile": pct,
                "beat_rate": pct,
            }
            print(f"  [{label}] {tag}: 随机中位数 {np.median(arr):+.2%} "
                  f"(p10 {np.percentile(arr, 10):+.2%} ~ p90 {np.percentile(arr, 90):+.2%}) "
                  f"| 策略 {st:+.2%} → 处于第 {pct:.0f} 百分位", flush=True)
    (OUT_DIR / f"random_control_000300_top{TOP_N}_reb{REB}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n>> 明细已保存: {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
