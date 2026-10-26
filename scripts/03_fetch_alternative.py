"""
第 3 步：抓取另类/深度数据——估值、财务、资金流、宏观、龙虎榜。
用法：python scripts/03_fetch_alternative.py
"""
import _bootstrap  # noqa: F401
import pandas as pd

from src.data.financials import fetch_all_financials
from src.data.macro import fetch_bs_macro, fetch_cpi, fetch_lpr
from src.data.moneyflow import (
    fetch_lhb,
    fetch_margin_hist,
    fetch_northbound,
)
from src.data.sources import bs_logout
from src.data.storage import ParquetStore
from src.data.valuation import fetch_index_valuation, fetch_stock_valuation

STOCKS = ["600519", "300750"]
INDEX_VAL_NAMES = ["沪深300", "中证500", "创业板50"]


def main() -> None:
    store = ParquetStore()

    print(">> [1/6] 个股估值（百度股市通）...")
    for s in STOCKS:
        df = fetch_stock_valuation(s, period="全部", store=store)
        print(f"  {s}: {len(df)} 行")

    print(">> [2/6] 指数估值 ...")
    for name in INDEX_VAL_NAMES:
        df = fetch_index_valuation(name, store=store)
        print(f"  {name}: {len(df)} 行")

    print(">> [3/6] 个股全套财务（摘要+季频六件套+快报预告）...")
    for s in STOCKS:
        r = fetch_all_financials(s, years=3, store=store)
        print(f"  {s}: 摘要 {r['abstract_rows']} 行, 季频 {r['baostock']}")

    print(">> [4/6] 北向资金 + 两融 ...")
    print(f"  北向: {len(fetch_northbound('北向资金', store))} 行")
    print(f"  沪市两融: {len(fetch_margin_hist('sh', store))} 行")
    print(f"  深市两融: {len(fetch_margin_hist('sz', store))} 行")

    print(">> [5/6] 近一个月龙虎榜 ...")
    end = pd.Timestamp.now()
    start = end - pd.Timedelta(days=30)
    lhb = fetch_lhb(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), store)
    print(f"  龙虎榜: {len(lhb)} 行")

    print(">> [6/6] 宏观数据（CPI/LPR + baostock 全套）...")
    print(f"  CPI: {len(fetch_cpi(store))} 行")
    print(f"  LPR: {len(fetch_lpr(store))} 行")
    bsm = fetch_bs_macro(store)
    for k, v in bsm.items():
        print(f"  {k}: {v}")

    print("\n>> 另类数据抓取完成。")
    bs_logout()


if __name__ == "__main__":
    main()
