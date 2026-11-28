"""
第 61 步：按日 ST 标记表回填 / 刷新（D-118，2026-11-26）。

为什么要有这张表：
  D-071 的 ST 剔除按**今天的简称**回溯全部历史——把尚未戴帽时期的持仓也抹掉了。
  2026-11-26 审计实测：策略持有过的 7 只现 ST 股，33 次持有全部早于戴帽日
  （苏宁易购 2022-05-06、中南建设 2024-04-24、阳光城 2023-05-05……）。
  baostock 日线自带 isST（当日是否 ST/*ST），是真正的时点数据。

做什么：
  对沪深300 历史成分并集逐只抓取 isST（一只一次请求），落 meta/st_flag.parquet
  （symbol, date, is_st）。断点续抓：已有记录的标的只补最后日期之后。
  build_factor_panel(exclude_st=True) 自 D-118 起读这张表，按**当日**状态剔除。

用法：
    python scripts/61_fetch_st_flag.py            # 沪深300 历史成分并集（约 949 只）
    python scripts/61_fetch_st_flag.py --zz500    # 另含中证500 历史成分并集（备料）
    python scripts/61_fetch_st_flag.py 30         # 只跑前 30 只（调试）
耗时：首次全量约 (0.5s + 0.35s)/只 ≈ 13 分钟；之后每次只补新日期，同样逐只一请求。
建议：随周六维护链每月刷一次（表停更期间，晚于表内最新日的股票日回退到今日名单口径，
方向保守）。退出码：有失败 exit 1。
"""
import sys

import _bootstrap  # noqa: F401

from config import STORE_DIR
from src.data import universe
from src.data.storage import ParquetStore


def main() -> None:
    # CODE_STANDARD §3 写仓过锁：本脚本整表覆盖 meta/st_flag，两个实例并发会互相
    # 覆盖（2026-11-26 实测：两次启动后表里只剩最后一批 50 只，2.4M 行的表被写坏）。
    from src.live.proclock import try_lock
    if try_lock(STORE_DIR / "sim" / ".pipeline.lock") is None:
        print(">> [lock] 另一个写入实例正在运行（或日更链在跑），本次退出", flush=True)
        sys.exit(3)

    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    if "--zz500" in sys.argv:
        symbols = sorted(set(symbols) | set(universe.pit_union_symbols("000905", store)))
    limit = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    if limit:
        symbols = symbols[:limit]
    print(f">> 按日 ST 标记表：{len(symbols)} 只（baostock isST，断点续抓）", flush=True)
    st = universe.update_st_flag(symbols, store=store)
    flag = store.read_or_empty("meta", universe.ST_FLAG_NAME)
    span = (f"{flag['date'].min().date()} ~ {flag['date'].max().date()}" if len(flag) else "空")
    print(f">> 完成：新抓 {st['updated']}、已最新 {st['uptodate']}、无记录 {st['empty']}、"
          f"失败 {len(st['failed'])} | 表内 {st['rows']} 行，{flag['symbol'].nunique() if len(flag) else 0} 只，"
          f"区间 {span}", flush=True)
    for code, err in st["failed"][:10]:
        print(f"   FAIL {code}: {err}", flush=True)
    sys.exit(1 if st["failed"] else 0)


if __name__ == "__main__":
    main()
