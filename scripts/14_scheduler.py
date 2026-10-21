# -*- coding: utf-8 -*-
"""
Python 原生定时调度器（APScheduler，常驻进程）。

与 Windows 任务计划的区别：
  - 调度逻辑在 Python 里（cron 表达式、错过补跑、合并、单实例）；
  - 需要本进程常驻（关掉窗口就停）；开机自启见 README/命令提示。

每天 19:00（Asia/Shanghai）执行：
  1) scripts/02b_update_pit.py        更新 PIT 全部成员行情（东财，禁用新浪降级）
  2) scripts/10_daily_run.py --skip-update   两个影子账户 + 日报 + 邮件

用法：
  python scripts/14_scheduler.py              # 常驻（Ctrl+C 退出）
  python scripts/14_scheduler.py --run-now    # 立即执行一次（测试用）
  python scripts/14_scheduler.py --hour 19 --minute 0
"""
import argparse
import datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOG = ROOT / "data" / "store" / "logs" / "scheduler.log"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def _run(cmd: list[str]) -> int:
    _log("run: " + " ".join(cmd))
    with LOG.open("a", encoding="utf-8") as f:
        p = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=f)
    _log(f"exit={p.returncode}")
    return p.returncode


def job() -> None:
    _log("== daily job start ==")
    _run([PY, "scripts/02b_update_pit.py"])
    _run([PY, "scripts/10_daily_run.py", "--skip-update"])
    _log("== daily job done ==")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-now", action="store_true", help="立即执行一次后退出")
    ap.add_argument("--hour", type=int, default=19)
    ap.add_argument("--minute", type=int, default=0)
    args = ap.parse_args()

    if args.run_now:
        job()
        return

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("缺少 apscheduler：先执行  pip install apscheduler")
        sys.exit(1)

    sched = BlockingScheduler(timezone="Asia/Shanghai")
    sched.add_job(job,
                  CronTrigger(hour=args.hour, minute=args.minute, timezone="Asia/Shanghai"),
                  id="quantdex_daily",
                  misfire_grace_time=6 * 3600,  # 错过 6 小时内补跑
                  coalesce=True,                # 多次错过只跑一次
                  max_instances=1,
                  replace_existing=True)
    _log(f"scheduler started: daily {args.hour:02d}:{args.minute:02d} Asia/Shanghai")
    print(f"调度器已启动：每天 {args.hour:02d}:{args.minute:02d}（Asia/Shanghai），Ctrl+C 退出")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        _log("scheduler stopped")
        print("调度器已停止")
