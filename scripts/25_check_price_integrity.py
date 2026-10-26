# -*- coding: utf-8 -*-
"""价格完整性检测器（D-041 的证据仪器）。

原理：除权日当天，真实市场涨跌幅（pct_chg，交易所口径）与存储价格的
close-to-close 变化应当一致；两者之差 = 复权断裂（假跳空）。
|断裂| > 1% 记一处。用分红除权表作为外部基准（内部自洽检查看不见它）。

用法：python scripts/25_check_price_integrity.py [--symbol 600519]
"""
import sys
from pathlib import Path

import pandas as pd

import _bootstrap  # noqa: F401
from config import STORE_DIR

TOL = 0.01


def check_symbol(sym: str, kline_path: Path, div_path: Path) -> int:
    k = pd.read_parquet(kline_path)
    k["date"] = pd.to_datetime(k["date"])
    k = k.sort_values("date").reset_index(drop=True)
    dv = pd.read_parquet(div_path)
    ex = pd.to_datetime(dv["dividOperateDate"], errors="coerce").dropna()
    cash = pd.to_numeric(dv["dividCashPsBeforeTax"], errors="coerce")
    events = set(ex[cash > 0].dt.date)
    if not events:
        return 0
    breaks = 0
    for i in range(1, len(k)):
        if k["date"].iloc[i].date() not in events:
            continue
        real = pd.to_numeric(k["pct_chg"].iloc[i], errors="coerce") / 100.0
        if pd.isna(real) or pd.isna(k["close"].iloc[i - 1]) or k["close"].iloc[i - 1] <= 0:
            continue
        stored = k["close"].iloc[i] / k["close"].iloc[i - 1] - 1.0
        if abs(stored - real) > TOL:
            breaks += 1
    return breaks


def main() -> None:
    only = None
    for a in sys.argv[1:]:
        if a.startswith("--symbol"):
            only = sys.argv[sys.argv.index(a) + 1]
    base = STORE_DIR / "financials" / "baostock"
    total = syms = 0
    worst = []
    for dv in sorted(base.glob("*__dividend.parquet")):
        sym = dv.stem.split("__")[0]
        if only and sym != only:
            continue
        kl = STORE_DIR / "market" / "daily" / f"{sym}__qfq.parquet"
        if not kl.exists():
            continue
        n = check_symbol(sym, kl, dv)
        syms += 1
        if n:
            total += n
            worst.append((n, sym))
    worst.sort(reverse=True)
    print(f"检测 {syms} 只（有分红记录且有行情）")
    print(f"复权断裂总数（|假跳空|>1% 的除权日）: {total}")
    for n, sym in worst[:10]:
        print(f"  {sym}: {n} 处")


if __name__ == "__main__":
    main()
