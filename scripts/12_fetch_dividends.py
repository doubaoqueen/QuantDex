"""
增量抓取 PIT 历史成员的分红送配数据（baostock，按报告年度）。

D-062 修复"存在即跳过"：分红表停更会让 dy_ttm 因子（红利账户在用）逐年失真、
25 号价格完整性检测的外部锚逐年缩水。现行为**保鲜策略**——
  · 无文件 → 抓取；
  · 60 天内抓过 → 跳过（fresh）；
  · 有分红记录但最新除权日已过 400 天（年度分红大概率缺新年份）→ 刷新；
  · 零分红记录且 180 天未抓 → 再核查（公司可能开始分红）。
用法：
    python scripts/12_fetch_dividends.py            # 保鲜刷新（缺+过期）
    python scripts/12_fetch_dividends.py 50         # 只跑前 50 只（调试）
输出：data/store/fin_bs/{code}__dividend.parquet
"""
import sys
import time

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data import universe
from src.data.financials import fetch_dividend
from src.data.storage import ParquetStore

YEARS = 8
FRESH_DAYS = 60          # 最近抓过就算新鲜
NEVER_PAY_RECHECK = 180  # 零分红记录的再核查周期（天）
MAX_DIV_AGE_DAYS = 400   # 最新除权日超过此天数 → 大概率缺新年份

_DIV_DIR = STORE_DIR / "financials" / "baostock"


def _needs_refresh(sym: str) -> tuple[bool, str]:
    """返回（是否需要抓取, 原因标签）。判据见模块 docstring 的保鲜策略。"""
    path = _DIV_DIR / f"{sym}__dividend.parquet"
    if not path.exists():
        return True, "new"
    age = time.time() - path.stat().st_mtime
    if age < FRESH_DAYS * 86400:
        return False, "fresh"
    try:
        df = pd.read_parquet(path, columns=["dividOperateDate"])
    except Exception:  # noqa: BLE001  读不动=坏了，重抓覆盖
        return True, "unreadable"
    newest = pd.to_datetime(df["dividOperateDate"], errors="coerce").max()
    if df.empty or pd.isna(newest):
        return age > NEVER_PAY_RECHECK * 86400, "never-paid"
    return (pd.Timestamp.now() - newest).days > MAX_DIV_AGE_DAYS, "stale-div"


def main() -> None:
    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if limit:
        symbols = symbols[:limit]
    print(f">> 分红抓取：{len(symbols)} 只（缺文件抓取 + 过期刷新，D-062）", flush=True)
    ok = skip = fail = 0
    for i, sym in enumerate(symbols, 1):
        try:
            need, why = _needs_refresh(sym)
            if not need:
                skip += 1
                continue
            fetch_dividend(str(sym), years=YEARS, store=store)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"   FAIL {sym}: {str(e)[:80]}", flush=True)
        if i % 25 == 0:
            print(f"   进度 {i}/{len(symbols)} | 刷新 {ok} 跳过 {skip} 失败 {fail}", flush=True)
    print(f">> 完成：刷新 {ok}，跳过 {skip}，失败 {fail}", flush=True)
    sys.exit(1 if fail else 0)  # 入调度后用退出码说话（D-062）


if __name__ == "__main__":
    main()
