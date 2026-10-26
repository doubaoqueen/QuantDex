# -*- coding: utf-8 -*-
"""
Python 原生定时调度器（APScheduler，常驻进程）。

与 Windows 任务计划的区别：
  - 调度逻辑在 Python 里（cron 表达式、错过补跑、合并、单实例）；
  - 需要本进程常驻（关掉窗口就停）；开机自启见 README/命令提示。

每天 19:00（Asia/Shanghai）执行：
  1) scripts/02b_update_pit.py        更新 PIT 全部成员行情（东财，禁用新浪降级）
  2) scripts/10_daily_run.py --skip-update   四个影子账户 + 合并日报 + 邮件

用法：
  python scripts/14_scheduler.py                  # 常驻（Ctrl+C 退出）；启动时若今天该跑还没跑，会立即补跑
  python scripts/14_scheduler.py --run-now        # 立即执行一次后退出（测试/手动补跑）
  python scripts/14_scheduler.py --hour 19 --minute 0
  python scripts/14_scheduler.py --no-catch-up    # 启动时不补跑（只等下一次定时）

2026-10-26 修复记录（用户反馈"运行 14 脚本没收到推送"）：
  - apscheduler 缺失时原来只打印一句话就退出、且不写日志 → 现在会写 scheduler.log 并以退出码 1 结束；
  - 新增启动补跑（catch-up）：当天已过 19:00 且今天还没有任何账户日报时，启动即补跑一次；
  - 日志同时记录每个子进程的退出码，便于定位"跑了一半被打断"。
"""
import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOG = ROOT / "data" / "store" / "logs" / "scheduler.log"
REPORTS = ROOT / "data" / "store" / "reports"
ACCOUNTS = ("large", "small", "div", "topk")


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def _run(cmd: list[str]) -> int:
    _log("run: " + " ".join(cmd))
    # 子进程输出直接写进 UTF-8 日志文件，必须让它也用 UTF-8（否则中文变乱码）
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with LOG.open("a", encoding="utf-8") as f:
        p = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=f, env=env)
    _log(f"exit={p.returncode}  ({Path(cmd[1]).name})")
    return p.returncode


def job() -> None:
    _log("== daily job start ==")
    rc_pit = _run([PY, "scripts/02b_update_pit.py"])
    if rc_pit != 0:
        _log(f"warn: PIT 更新失败 rc={rc_pit}，仍继续跑影子盘（用本地已有数据）")
    rc_daily = _run([PY, "scripts/10_daily_run.py", "--skip-update"])
    _log(f"== daily job done (pit={rc_pit}, daily={rc_daily}) ==")
    if rc_daily != 0:
        _log("ERROR: 日报流程未正常结束，邮件可能未发送；详见 reports 目录与下方子进程输出")


def _report_exists_today() -> bool:
    today = datetime.date.today().strftime("%Y-%m-%d")
    return any((REPORTS / acc / f"{today}.md").exists() for acc in ACCOUNTS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-now", action="store_true", help="立即执行一次后退出")
    ap.add_argument("--hour", type=int, default=19)
    ap.add_argument("--minute", type=int, default=0)
    ap.add_argument("--no-catch-up", action="store_true",
                    help="启动时不检查/补跑今天错过的任务")
    args = ap.parse_args()

    if args.run_now:
        job()
        return

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as e:
        msg = (f"FATAL: apscheduler 未安装（{e}）。调度器无法启动。\n"
               f"请先执行：{PY} -m pip install apscheduler")
        _log(msg)
        print(msg)
        print("提示：也可以不装 apscheduler，直接用 Windows 计划任务（scripts/13_setup_task.ps1）")
        sys.exit(1)

    now = datetime.datetime.now()
    scheduled = now.replace(hour=args.hour, minute=args.minute, second=0, microsecond=0)

    # 启动补跑：今天该跑的时间已过、且今天还没有任何账户日报 → 立即补一次
    if not args.no_catch_up and now >= scheduled and not _report_exists_today():
        _log(f"catch-up: {scheduled:%Y-%m-%d %H:%M} 已过且今日无日报，启动即补跑")
        print(f"检测到今天 {args.hour:02d}:{args.minute:02d} 已过且还没有日报，先补跑一次...")
        job()
    elif _report_exists_today():
        print(f"今天（{now:%Y-%m-%d}）的日报已存在，等待下一次定时 {args.hour:02d}:{args.minute:02d}")
        _log(f"startup: 今日日报已存在，跳过补跑")

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
    print(f"日志：{LOG}")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        _log("scheduler stopped")
        print("调度器已停止")


if __name__ == "__main__":
    main()
