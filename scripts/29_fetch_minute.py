"""分时数据回填/增量：baostock 5 分钟线（2011 年至今全历史，本仓库默认从 2018 起）。

为什么存 5 分钟线（D-060）：三个具体消费者——
  ① 滑点校准：回测/影子盘的 0.0005 是估计值，用真实冲击成本校准；
  ② 日内因子研究：隔夜跳空/日内波动族因子（expr 因子家族的延伸）；
  ③ 执行形态研究：INTRADAY_EXECUTION_DESIGN 的 S1（11 时点研究）的数据地基。
盯盘不需要：T+1 开盘撮合下盘中没有可执行决策（D-022/026/033 三轮回测
否定全部盘中反应规则）——存数据 ≠ 盯盘，采集发生在收盘后。

速率现实与多进程分片（D-062）：全史抓取约 3.5 分钟/只（10 万行逐行传输，
协议所限），单进程 949 只约 2 天——太快不了的原因在 baostock 单连接协议。
实测同机多进程各自 login 可并发（2026-11-07），故父进程持 .minute.lock 后
派生 N 个 worker（各自独立 bs 连接，分片 symbols[idx::N]），默认 N=3
（环境变量 QUANTDEX_MINUTE_SHARDS 调整；并发过高可能触发服务端限流，
worker 失败清单会暴露）。双向断点续传：重跑时已完成的秒级跳过，
向前补最新、向后补早段。

口径：不复权（adjust=none），理由见 fetch_minute_bs docstring。
频率：默认 5 分钟；1 分钟线无免费全历史源且当前无消费者，不抓（避免
重蹈"抓而不用"的闲置数据堆积——见 2026-11-07 审计报告第三节）。

用法：
    python scripts/29_fetch_minute.py              # 全 PIT 池，从 20180101（双向续传，3 进程）
    python scripts/29_fetch_minute.py 20110101     # 指定更早起始日（baostock 5 分钟线自 2011 起）
    python scripts/29_fetch_minute.py 20180101 5   # 只跑前 5 只（调试）
"""
import faulthandler
import os
import subprocess
import sys

import _bootstrap  # noqa: F401


# 卡死诊断（2026-11-09 夜间卡死 2h、原因未定位）：看门狗线程每 30 分钟把全线程
# 堆栈追加到文件——卡死时主线程阻塞也能转储（本机 Anaconda 构建的 faulthandler
# 无 register/信号接口，只能用定时转储）。卡死即看 logs/minute_worker_*.stack。
def _arm_stack_dump(tag: str) -> None:
    fh = open(STORE_DIR / "logs" / f"minute_worker_{tag}_{os.getpid()}.stack", "w",
              encoding="utf-8")
    faulthandler.dump_traceback_later(1800, repeat=True, file=fh)

from config import STORE_DIR
from src.data import universe
from src.data.market import update_minute_batch
from src.data.storage import ParquetStore


def _run_shard(store: ParquetStore, symbols: list[str], start: str) -> int:
    """跑一个分片，返回失败数（worker 模式与单进程模式共用）。"""
    r = update_minute_batch(symbols, freq="5", full_start=start,
                            store=store, backfill_missing=True)
    failed = r.pop("__failed__", [])
    n_up = sum(1 for v in r.values() if v == "uptodate")
    rows = sum(v for v in r.values() if isinstance(v, int))
    print(f">> 分片完成：处理 {len(r)} 只（已最新 {n_up}），新增 {rows} 行，"
          f"失败 {len(failed)} 只", flush=True)
    for code, err in failed[:10]:
        print(f"   FAIL {code}: {err[:120]}", flush=True)
    return len(failed)


def main() -> None:
    args = sys.argv[1:]

    # ---- worker 模式（由父进程派生，不拿锁——锁在父进程手里）----
    if "--worker" in args:
        i = args.index("--worker")
        shard_idx, n_shards = int(args[i + 1]), int(args[i + 2])
        rest = args[:i] + args[i + 3:]
        start = rest[0] if rest else "20180101"
        store = ParquetStore()
        symbols = universe.pit_union_symbols("000300", store)
        mine = symbols[shard_idx::n_shards]
        print(f">> [worker {shard_idx + 1}/{n_shards}] 分到 {len(mine)} 只", flush=True)
        _arm_stack_dump(f"w{shard_idx + 1}")
        sys.exit(1 if _run_shard(store, mine, start) else 0)

    # ---- 父进程：持 .minute.lock（只与每日调度的分钟增量互斥；不拿
    # .pipeline.lock——回填以小时计，绝不能挡住 19:00 的日线/流水线/邮件）----
    from src.live.proclock import try_lock
    lf = try_lock(STORE_DIR / "sim" / ".minute.lock")
    if lf is None:
        print(">> [lock] 另一个分钟线任务（回填或当晚增量）正在运行，本次退出")
        sys.exit(3)

    start = args[0] if args else "20180101"
    limit = int(args[1]) if len(args) > 1 else 0
    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    if limit:
        symbols = symbols[:limit]
    n_shards = int(os.environ.get("QUANTDEX_MINUTE_SHARDS", "2"))  # D-071 拉黑教训：默认 2 并发
    print(f">> 5 分钟线回填：{len(symbols)} 只，起始 {start}，{n_shards} 进程分片"
          f"（双向断点续传）", flush=True)
    if n_shards <= 1:
        _arm_stack_dump("solo")
        sys.exit(1 if _run_shard(store, symbols, start) else 0)

    procs = [subprocess.Popen(
        [sys.executable, os.path.abspath(__file__),
         "--worker", str(k), str(n_shards), start])
        for k in range(n_shards)]
    rcs = [p.wait() for p in procs]
    print(f">> 全部 worker 完成，退出码 {rcs}（1=该分片有失败，重跑自动续传）",
          flush=True)
    sys.exit(1 if any(rcs) else 0)


if __name__ == "__main__":
    main()
