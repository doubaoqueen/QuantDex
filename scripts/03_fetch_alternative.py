"""
第 3 步：另类/深度数据批量抓取——估值、财务、资金流、宏观、龙虎榜。

2026-11-14 重构（P1-a/P1-b，TODO 数据补全节）：个股估值与财务从 2 只演示改为
按 PIT 历史成分并集（949 只）批量，支持断点续传——fin_abstract / valuation
已入库的标的自动跳过，失败清单收尾打印，重跑自动补漏（D-062 失败帧纪律）。

用法：
    python scripts/03_fetch_alternative.py                 # 全量（市场级 + 个股批量）
    python scripts/03_fetch_alternative.py --limit 3       # 个股批量只跑前 3 只（试跑）
    python scripts/03_fetch_alternative.py --skip-market   # 只跑个股批量
    python scripts/03_fetch_alternative.py --skip-batch    # 只跑市场级（宏观/北向/两融/龙虎榜/指数估值）

耗时预估：财务六件套（baostock，串行）949 只约 2~3h；估值（百度）约 20~30min。
baostock 查询超时天会大量失败——断点续传设计，改天重跑自动补漏，不要硬冲。
"""
import argparse
import time

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
from src.data.universe import pit_union_symbols
from src.data.valuation import fetch_index_valuation, fetch_stock_valuation

INDEX_VAL_NAMES = ["沪深300", "中证500", "创业板50"]
REQUEST_INTERVAL = 0.35                 # config.REQUEST_INTERVAL 同值：百度接口礼貌间隔


def market_level(store: ParquetStore) -> None:
    """市场级数据：与个股无关，全量刷新（北向/两融/龙虎榜/宏观/指数估值）。"""
    print(">> [市场级] 指数估值 ...")
    for name in INDEX_VAL_NAMES:
        df = fetch_index_valuation(name, store=store)
        print(f"  {name}: {len(df)} 行")

    print(">> [市场级] 北向资金 + 两融 ...")
    print(f"  北向: {len(fetch_northbound('北向资金', store))} 行")
    print(f"  沪市两融: {len(fetch_margin_hist('sh', store))} 行")
    print(f"  深市两融: {len(fetch_margin_hist('sz', store))} 行")

    print(">> [市场级] 近一个月龙虎榜 ...")
    end = pd.Timestamp.now()
    start = end - pd.Timedelta(days=30)
    lhb = fetch_lhb(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), store)
    print(f"  龙虎榜: {len(lhb)} 行")

    print(">> [市场级] 宏观数据（CPI/LPR + baostock 全套）...")
    print(f"  CPI: {len(fetch_cpi(store))} 行")
    print(f"  LPR: {len(fetch_lpr(store))} 行")
    bsm = fetch_bs_macro(store)
    for k, v in bsm.items():
        print(f"  {k}: {v}")


def _batch(symbols, fetch_one, exists_check, label, limit):
    """通用批量骨架：断点续传 + 失败清单 + 进度打印（D-062 失败帧纪律）。"""
    todo = symbols[:limit] if limit else symbols
    n = len(todo)
    print(f">> [个股批量] {label}：{n} 只（其中已入库跳过若干）")
    done, skipped, failed, empty = 0, 0, [], []
    for i, sym in enumerate(todo, 1):
        if exists_check(sym):
            skipped += 1
            continue
        try:
            rows = fetch_one(sym)
            # 返回值可能是 DataFrame（估值）或 dict（财务统计）——统一按长度判空
            if rows is None or (hasattr(rows, "__len__") and len(rows) == 0):
                empty.append(sym)
            else:
                done += 1
        except Exception as e:  # noqa: BLE001  单只失败不拖垮批次，错误清单收尾打印
            failed.append((sym, str(e)[:120]))
        if i % 25 == 0 or i == n:
            print(f"  进度 {i}/{n} | 新入库 {done} | 跳过 {skipped} "
                  f"| 空 {len(empty)} | 失败 {len(failed)}", flush=True)
        time.sleep(REQUEST_INTERVAL)
    print(f">> [个股批量] {label} 完成：新入库 {done} | 跳过（已存在）{skipped} "
          f"| 空数据 {len(empty)} | 失败 {len(failed)}")
    if failed:
        print("  失败清单（重跑本脚本自动补漏）：")
        for sym, err in failed[:20]:
            print(f"    {sym}: {err}")
        if len(failed) > 20:
            print(f"    ……另有 {len(failed) - 20} 只")
    return len(failed)


def batch_stocks(store: ParquetStore, symbols, limit: int) -> None:
    """个股批量：估值（百度）+ 财务全套（baostock），各自独立断点续传。"""
    print(f">> [个股批量] 股票池 = PIT 历史成分并集：{len(symbols)} 只")

    fail_a = _batch(
        symbols,
        lambda s: fetch_stock_valuation(s, period="全部", store=store),
        lambda s: store.exists("valuation", s),
        "估值（百度，P1-a）", limit)

    fail_b = _batch(
        symbols,
        lambda s: fetch_all_financials(s, years=3, store=store),
        lambda s: store.exists("fin_abstract", s),
        "财务全套（baostock，P1-b：摘要+六件套+快报预告+分红+复权因子）", limit)

    if fail_a or fail_b:
        print(f"\n>> 注意：本轮失败 {fail_a + fail_b} 只——多为数据源超时，"
              f"重跑本脚本自动补漏（断点续传）。")


def main() -> None:
    ap = argparse.ArgumentParser(description="另类/深度数据批量抓取")
    ap.add_argument("--limit", type=int, default=0, help="个股批量只跑前 N 只（0=全部，试跑用）")
    ap.add_argument("--skip-market", action="store_true", help="跳过市场级（北向/两融/龙虎榜/宏观/指数估值）")
    ap.add_argument("--skip-batch", action="store_true", help="跳过个股批量（只刷市场级）")
    args = ap.parse_args()

    store = ParquetStore()
    if not args.skip_market:
        market_level(store)
    if not args.skip_batch:
        symbols = pit_union_symbols("000300", store)
        batch_stocks(store, symbols, args.limit)

    print("\n>> 另类数据抓取完成。")
    bs_logout()


if __name__ == "__main__":
    main()
