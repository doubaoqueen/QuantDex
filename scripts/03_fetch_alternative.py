"""
第 3 步：另类/深度数据批量抓取——估值、财务、资金流、宏观、龙虎榜。

2026-11-14 重构（P1-a/P1-b）：个股估值与财务从 2 只演示改为按 PIT 历史成分并集
（949 只）批量，断点续传 + 落盘核验 + 限流熔断。

2026-11-15 首轮教训修订（验收打脸三条，全部内建，见 TODO P1-b 条目）：
  ① 节流预算：标的服务间隔 0.35s → 1.2s（每标的一轮 ≈ 10 个 bs 查询，0.35s
     等于 0.035s/查询——D-060 锤法复刻，教训重蹈）；连续失败熔断（15 只冷却
     120s，40 只中止——限流时硬冲 = 拉黑复刻）。
  ② 断点锚点改为 fin_bs 件数完整性（≥8 件：六件套+分红+复权因子）——摘要存在
     ≠ 六件套完整，首轮 830 只部分完成态会被旧锚点误跳过。
  ③ 落盘核验：完成计数来自实存文件数，不来自"fetch 没抛异常"（首轮进度行
     "失败 0" 与日志 62040 行连接错误矛盾）。

用法：
    python scripts/03_fetch_alternative.py                 # 全量（市场级 + 个股批量）
    python scripts/03_fetch_alternative.py --limit 3       # 个股批量只跑前 3 只（试跑）
    python scripts/03_fetch_alternative.py --skip-market   # 只跑个股批量
    python scripts/03_fetch_alternative.py --skip-batch    # 只跑市场级（宏观/北向/两融/龙虎榜/指数估值）

耗时预估：财务六件套（baostock，串行）949 只约 3h（1.2s 间隔下）；估值（百度）约 30min。
baostock 限流天熔断会中止——冷却后重跑，断点续传自动补漏，不要硬冲（D-060）。
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
VAL_INTERVAL = 0.35                    # 百度接口礼貌间隔
BS_SYMBOL_INTERVAL = 1.2               # baostock 标的服务间隔（每标的一轮 ≈ 10 查询）
BS_COMPLETE_MIN_FILES = 8              # 六件套6 + 分红1 + 复权因子1 = 8 件为完整
BS_COOLDOWN_AFTER = 15                 # 连续失败 N 只 → 冷却 120s
BS_ABORT_AFTER = 40                    # 连续失败 N 只（冷却后仍持续）→ 中止批次


def market_level(store: ParquetStore) -> None:
    """市场级数据：与个股无关，全量刷新（北向/两融/龙虎榜/宏观/指数估值）。"""
    print(">> [市场级] 指数估值 ...")
    for name in INDEX_VAL_NAMES:
        df = fetch_index_valuation(name, store=store)
        print(f"  {name}: {len(df)} 行")

    print(">> [市场级] 北向资金 + 两融 ...")
    print(f"  北向: {len(fetch_northbound('北向资金', store))} 行")
    print(f"  沪市两融: {len(fetch_margin_hist('sh', store))} 行")
    print(f"  沪市两融(深): {len(fetch_margin_hist('sz', store))} 行")

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


def _finbs_count(store: ParquetStore, symbol: str) -> int:
    """该标的在 financials/baostock 下的实存文件数（六件套+分红+复权因子的落盘核验）。"""
    from config import FINANCIAL_DIR
    from src.data.schema import normalize_code
    d = FINANCIAL_DIR / "baostock"
    code = normalize_code(symbol)
    return len(list(d.glob(f"{code}__*.parquet")))


def _batch(symbols, fetch_one, done_check, label, limit, interval):
    """通用批量骨架：断点续传（完整锚点）+ 落盘核验计数 + 限流熔断（D-060 教训内建）。"""
    todo = symbols[:limit] if limit else symbols
    n = len(todo)
    print(f">> [个股批量] {label}：{n} 只（其中已完整跳过若干）")
    done, skipped, failed, empty = 0, 0, [], []
    consec_fail = 0
    for i, sym in enumerate(todo, 1):
        if done_check(sym):
            skipped += 1
            continue
        try:
            fetch_one(sym)
            landed = done_check(sym)               # ③ 落盘核验：不信任"没抛异常"
            if landed:
                done += 1
                consec_fail = 0
            else:
                failed.append((sym, "fetch 返回但落盘不完整"))
                consec_fail += 1
        except Exception as e:  # noqa: BLE001  单只失败不拖垮批次
            failed.append((sym, str(e)[:120]))
            consec_fail += 1
        if i % 25 == 0 or i == n:
            print(f"  进度 {i}/{n} | 落盘核验完成 {done} | 跳过 {skipped} "
                  f"| 空 {len(empty)} | 失败 {len(failed)}", flush=True)
        # ① 限流熔断（D-060）：连续失败达阈值 → 冷却；冷却后仍连续失败达硬上限 → 中止
        if consec_fail >= BS_ABORT_AFTER:
            print(f"  [熔断中止] 连续 {consec_fail} 只失败且冷却无效——baostock 已限流，"
                  f"冷却后重跑自动补漏（断点续传）", flush=True)
            break
        if consec_fail >= BS_COOLDOWN_AFTER:
            print(f"  [冷却] 连续 {consec_fail} 只失败，暂停 120s（D-060 拉黑教训：限流时硬冲 = 拉黑复刻）",
                  flush=True)
            time.sleep(120)
            consec_fail = 0
        time.sleep(interval)
    print(f">> [个股批量] {label} 完成：落盘核验完成 {done} | 跳过 {skipped} "
          f"| 空 {len(empty)} | 失败 {len(failed)}")
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
        "估值（百度，P1-a）", limit, VAL_INTERVAL)

    fail_b = _batch(
        symbols,
        lambda s: fetch_all_financials(s, years=3, store=store),
        lambda s: _finbs_count(store, s) >= BS_COMPLETE_MIN_FILES,
        "财务全套（baostock，P1-b：摘要+六件套+快报预告+分红+复权因子）", limit, BS_SYMBOL_INTERVAL)

    if fail_a or fail_b:
        print(f"\n>> 注意：本轮失败 {fail_a + fail_b} 只——重跑本脚本自动补漏（断点续传）。")


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
