# -*- coding: utf-8 -*-
"""
第 4 步：因子研究（IC / 分层回测 / 衰减）。
流程：取指数成分股 -> 批量(增量)抓前复权日线 -> 构截面因子面板 -> 有效性检验 -> 结果落盘。

用法：
    python scripts/04_factor_research.py               # 默认沪深300，最多300只
    python scripts/04_factor_research.py 000905 200    # 中证500，取前200只
"""
import sys
import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.storage import ParquetStore
from src.data import universe
from src.data.market import update_kline_batch
from src.analysis.factors import FACTOR_NAMES, FACTOR_DESC, build_factor_panel
from src.analysis.factor_test import run_factor_tests

START_DATE = "20180101"     # 因子样本起始
N_GROUPS = 5                # 分层组数


def prepare_symbols(store: ParquetStore, index_code: str, limit: int) -> list[str]:
    cons_name = f"constituents_{index_code}"
    if not store.exists("meta", cons_name):
        print(f">> 抓取 {index_code} 成分股 ...")
        universe.fetch_index_constituents(index_code, store=store)
    cons = store.read("meta", cons_name)
    symbols = cons["symbol"].dropna().tolist()
    return symbols[:limit]


def show_results(res: dict) -> None:
    ic = res["ic_table"]
    mean_w = ic.pivot(index="factor", columns="horizon", values="ic_mean").round(4)
    icir_w = ic.pivot(index="factor", columns="horizon", values="icir").round(2)

    print("\n== RankIC 均值（持有周期 1/5/10/20 日，体现衰减）==")
    print(mean_w.to_string())
    print("\n== ICIR（IC 均值/标准差，衡量稳定性）==")
    print(icir_w.to_string())

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


def save_results(res: dict) -> "pathlib.Path":
    out = STORE_DIR / "factor_results"
    out.mkdir(parents=True, exist_ok=True)
    res["ic_table"].to_parquet(out / "ic_summary.parquet", index=False)
    for f, q in res["quantile"].items():
        q["nav"].to_parquet(out / f"nav_{f}.parquet")
        q["nav_ls"].rename("ls").to_frame().to_parquet(out / f"navls_{f}.parquet")
    return out


def main() -> None:
    index_code = sys.argv[1] if len(sys.argv) > 1 else "000300"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    store = ParquetStore()
    symbols = prepare_symbols(store, index_code, limit)
    print(f">> 股票池：{index_code} 成分 {len(symbols)} 只，"
          f"更新日线（自 {START_DATE}，可增量重复运行）...")
    r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                           full_start=START_DATE, source="sina", store=store)
    failed = r.pop("__failed__", [])
    print(f">> 日线就绪：成功 {len(r)}，失败 {len(failed)}")
    for code, err in failed[:10]:
        print(f"   FAIL {code}: {err}")

    print(">> 构建因子面板并检验 ...")
    panel = build_factor_panel(store, symbols, start=START_DATE)
    print(f"   面板 {panel['date'].min().date()} ~ {panel['date'].max().date()}，"
          f"{panel['symbol'].nunique()} 只，{len(panel)} 行")

    res = run_factor_tests(panel, factors=FACTOR_NAMES, n_groups=N_GROUPS)
    show_results(res)
    out = save_results(res)
    print(f"\n>> 结果已保存到 {out}")


if __name__ == "__main__":
    main()
