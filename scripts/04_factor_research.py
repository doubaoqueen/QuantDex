# -*- coding: utf-8 -*-
"""
第 4 步：因子研究（IC / 分层回测 / 衰减）。
流程：PIT 历史成分并集 -> 批量(增量)抓前复权日线(baostock，覆盖退市股)
      -> 构截面因子面板(point-in-time 过滤) -> 有效性检验 -> 结果落盘。

数据源说明：主源 baostock（稳、覆盖已退市股；东财对退市股无数据）。
不用新浪：其依赖 py_mini_racer(V8)，在本机会原生段错误（DECISIONS D-008）。

用法：
    python scripts/04_factor_research.py               # 沪深300，全部历史成员(需先跑 00 脚本)
    python scripts/04_factor_research.py 000905        # 中证500
    python scripts/04_factor_research.py 000300 100    # 只取前100只(快速冒烟，结果有偏)
"""
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.storage import ParquetStore
from src.data import universe
from src.data.market import get_local_last_date, update_kline_batch
from src.data.schema import normalize_code
from src.analysis.factors import FACTOR_NAMES, FACTOR_DESC, build_factor_panel
from src.analysis.factor_test import run_factor_tests

START_DATE = "20180101"     # 因子样本起始
N_GROUPS = 5                # 分层组数


def prepare_symbols(store: ParquetStore, index_code: str, limit: int):
    """返回 (symbols, pit_df)：PIT 模式用历史成分并集 + 成分表，缺 PIT 表则回退当前成分。"""
    try:
        symbols = universe.pit_union_symbols(index_code, store)
        pit = universe.load_pit_constituents(index_code, store)
        print(f">> PIT 模式：{index_code} 历史成分并集 {len(symbols)} 只（含已剔除/退市）")
    except FileNotFoundError:
        cons_name = f"constituents_{index_code}"
        if not store.exists("meta", cons_name):
            print(f">> 抓取 {index_code} 成分股 ...")
            universe.fetch_index_constituents(index_code, store=store)
        cons = store.read("meta", cons_name)
        symbols = cons["symbol"].dropna().tolist()
        pit = None
        print(f">> 回退模式（无 PIT 表）：当前成分 {len(symbols)} 只，存在幸存者偏差")
    return symbols[:limit] if limit else symbols, pit


def show_results(res: dict) -> None:
    ic = res["ic_table"]
    mean_w = ic.pivot(index="factor", columns="horizon", values="ic_mean").round(4)
    icir_w = ic.pivot(index="factor", columns="horizon", values="icir").round(2)

    print("\n== RankIC 均值（持有周期 1/5/10/20 日，体现衰减）==")
    print(mean_w.to_string())
    print("\n== ICIR（IC 均值/标准差，衡量稳定性）==")
    print(icir_w.to_string())
    nw_w = ic.pivot(index="factor", columns="horizon", values="nw_tstat").round(2)
    print("\n== Newey-West t 值（重叠持有期下自相关一致，|t|>2 才算显著）==")
    print(nw_w.to_string())

    rows = []
    for f in FACTOR_NAMES:
        q = res["quantile"][f]
        st = q["stats"]
        hi, lo = q["hi_col"], q["lo_col"]
        rows.append({
            "factor": f,
            "Qlow_total": st.loc[lo, "total"],
            "Qhigh_total": st.loc[hi, "total"],
            "long_short": q["nav_ls"].iloc[-1] - 1,
            "Qhigh_sharpe": st.loc[hi, "sharpe"],
            "mono_corr": q["mono_corr"],
            "strict_mono": q["strict_monotonic"],
        })
    table = pd.DataFrame(rows)
    print("\n== 分层回测（最低组 / 最高组 / 多空 / 单调性）==")
    print(table.round(4).to_string(index=False))

    print("\n因子含义：")
    for f in FACTOR_NAMES:
        print(f"  {f:<12}{FACTOR_DESC[f]}")


def save_results(res: dict) -> Path:
    out = STORE_DIR / "factor_results"
    out.mkdir(parents=True, exist_ok=True)
    res["ic_table"].to_parquet(out / "ic_summary.parquet", index=False)
    for f, q in res["quantile"].items():
        q["nav"].to_parquet(out / f"nav_{f}.parquet")
        q["nav_ls"].rename("ls").to_frame().to_parquet(out / f"navls_{f}.parquet")

    # 自动出图（失败不影响研究结果落盘）
    try:
        from src.analysis.plot import plot_ic_heatmap, plot_quantile_nav
        plot_ic_heatmap(res["ic_table"], out_dir=out / "plots")
        for f, q in res["quantile"].items():
            plot_quantile_nav(q["nav"], title=f"{f} 分层净值",
                              name=f, out_dir=out / "plots")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 出图失败（不影响结果落盘）: {e}")
    return out


def main() -> None:
    index_code = sys.argv[1] if len(sys.argv) > 1 else "000300"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0   # 0 = 全部历史成员

    store = ParquetStore()
    symbols, pit = prepare_symbols(store, index_code, limit)
    print(f">> 股票池 {len(symbols)} 只，更新日线（自 {START_DATE}，可增量重复运行）...")
    r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                           full_start=START_DATE, source="bs", store=store,
                           sina_fallback=False, bs_fallback=False)
    failed = r.pop("__failed__", [])
    print(f">> 日线就绪（baostock）：成功 {len(r)}，失败 {len(failed)}")

    # 兜底：主源失败（__failed__）或本地仍无数据的标的，逐只用 baostock 重试。
    # （旧新浪口径下退市股静默返回空需要此步；baostock 主源下通常只剩个别
    #   2008 年前退市/合并股——无任何数据源，属已知边界。）
    no_data = [s for s in symbols
               if get_local_last_date(normalize_code(s), "daily", "qfq", store) is None]
    bs_candidates = sorted({c for c, _ in failed} | set(no_data))
    if bs_candidates:
        print(f">> 逐只重试无数据/失败标的 {len(bs_candidates)} 只 ...")
        bs_fail = []
        for i, code in enumerate(bs_candidates, 1):
            try:
                from src.data import market
                df = market.fetch_kline(code, start=START_DATE, asset="stock",
                                        adjust="qfq", source="bs", store=store)
                if df is None or df.empty:
                    bs_fail.append((code, "空"))
            except Exception as e:  # noqa: BLE001
                bs_fail.append((code, str(e)[:60]))
            if i % 20 == 0 or i == len(bs_candidates):
                print(f"   bs 补齐进度 {i}/{len(bs_candidates)}，仍失败 {len(bs_fail)}")
        failed = bs_fail
    for code, err in failed[:10]:
        print(f"   FAIL {code}: {err}")

    print(">> 构建因子面板并检验（PIT 过滤已启用）...")
    panel = build_factor_panel(store, symbols, start=START_DATE,
                               pit_constituents=pit)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{panel['symbol'].nunique()} 只，{len(panel)} 行")

    res = run_factor_tests(panel, factors=FACTOR_NAMES, n_groups=N_GROUPS)
    show_results(res)
    out = save_results(res)
    print(f"\n>> 结果已保存到 {out}")


if __name__ == "__main__":
    main()
