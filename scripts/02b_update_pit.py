# -*- coding: utf-8 -*-
"""
只更新 PIT 历史成员行情（不跑流水线）。

用途：
  - 定时任务的第一步（与影子盘流水线解耦：更新失败不影响账户记录）；
  - 默认走 baostock（source=bs，实测 949 只约 3~5 分钟、失败 0，见 D-012：
    东财对本机逐只请求失败，含重试约 7.8 秒/只）；
  - 新浪降级默认禁用（QUANTDEX_SINA_FALLBACK=1 才启用）：其依赖 py_mini_racer(V8)，
    在本机会原生段错误（D-008）。
  - 可用环境变量切换：QUANTDEX_SOURCE=em（东财）/ bs（baostock，默认）。

用法：
  python scripts/02b_update_pit.py            # 全量增量
  python scripts/02b_update_pit.py 5          # 只更新前 5 只（调试）
"""
import os
import sys

import pandas as pd

import _bootstrap  # noqa: F401

from config import DEFAULT_START_DATE
from src.data import universe
from src.data.market import fetch_kline, update_kline_batch
from src.data.storage import ParquetStore


def main() -> None:
    store = ParquetStore()
    # R3-15：日历止于 2026-12-31 的定时炸弹——距到期 30 天内自动刷新（幂等）
    cal = store.read_or_empty("meta", "trade_calendar")
    if cal.empty or pd.to_datetime(cal["date"]).max() < pd.Timestamp.now().normalize() + pd.Timedelta(days=30):
        print(">> 交易日历缺失/将到期，刷新...", flush=True)
        try:
            from src.data.universe import fetch_trade_calendar
            fetch_trade_calendar(store)
        except Exception as e:  # noqa: BLE001
            print(f"[calendar] 刷新失败: {e}", flush=True)
    symbols = universe.pit_union_symbols("000300", store)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if limit:
        symbols = symbols[:limit]
    source = os.environ.get("QUANTDEX_SOURCE", "bs")
    allow_sina = os.environ.get("QUANTDEX_SINA_FALLBACK", "0") == "1"
    allow_bs = os.environ.get("QUANTDEX_BS_FALLBACK", "1") == "1"
    print(f">> 更新 PIT 行情：{len(symbols)} 只 | source={source} | "
          f"新浪降级={allow_sina} | baostock兜底={allow_bs}", flush=True)
    r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                           full_start=DEFAULT_START_DATE, source=source,
                           store=store, workers=4, sina_fallback=allow_sina,
                           bs_fallback=allow_bs)
    failed = r.pop("__failed__", [])
    n_up = sum(1 for v in r.values() if v == "uptodate")
    n_bs = sum(1 for v in r.values() if isinstance(v, str) and v.startswith("bs_fallback"))
    n_sina = sum(1 for v in r.values() if isinstance(v, str) and v.startswith("sina_fallback"))
    print(f">> 完成：处理 {len(r)} 只（已最新 {n_up}、baostock兜底 {n_bs}、新浪兜底 {n_sina}），"
          f"失败 {len(failed)} 只", flush=True)
    for code, err in failed[:10]:
        print(f"   FAIL {code}: {str(err)[:100]}", flush=True)

    # R3-02/D-041a 护栏：增量写入后行级自洽校验（序列涨跌 vs pct_chg，阈值 0.5%），
    # 破裂=除权锚点断裂，自动全史重抓修复——除权季每期制造的新缺口当日清零
    ok_syms = [c for c, v in r.items() if isinstance(v, int) and v > 0]
    broken = []
    for c in ok_syms:
        pth = store._path("daily", f"{c}__qfq")
        if not pth.exists():
            continue
        k = pd.read_parquet(pth, columns=["date", "close", "pct_chg"])
        if len(k) < 2:
            continue
        chg = k["close"] / k["close"].shift(1) - 1
        pct = pd.to_numeric(k["pct_chg"], errors="coerce") / 100
        if ((chg - pct).abs() > 0.005).any():
            broken.append(c)
    if broken:
        print(f"[guardrail] 自洽破裂 {len(broken)} 只（除权锚点断裂），全史重抓修复", flush=True)
        for c in broken:
            try:
                fetch_kline(c, start=DEFAULT_START_DATE, asset="stock", adjust="qfq",
                            source="bs", store=store)
            except Exception as e:  # noqa: BLE001
                print(f"[guardrail] {c} 重抓失败: {e}", flush=True)
        print(f"[guardrail] 完成（{len(broken)} 只）", flush=True)

    # 基准指数不在股票池里，必须单独更新（否则报告/市场状态一直是旧值）
    from src.data.snapshot import update_indexes
    print(">> 更新基准指数（沪深300 / 中证500）...", flush=True)
    ir = update_indexes(store)
    print(f">> 指数完成：{len(ir)} 个", flush=True)


if __name__ == "__main__":
    main()
