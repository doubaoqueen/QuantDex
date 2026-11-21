"""统一日志入口（Tier-2 logging 框架第一片，D-111，2026-11-22）。

动机：全库脚本以 print + shell 重定向写日志（run_daily.bat 三段式 → daily_run.log），
无时间戳、无级别——D-078 静默硬崩（exit 127）与 D-106 口径错位事故里，"什么时候
发生/发生在哪一阶段"都要靠猜。本模块提供统一格式与文件落点。

用法（研究脚本模式，53 号为模板）：
    from src.logsetup import get_logger
    log = get_logger(__file__)            # 控制台 + data/store/logs/{脚本名}.log
    log.info(">> 完成 %s 只", n)          # 替换 print

日更链（02b/10 号）暂不转换：周一 19:00 无人值守运行前不做无测试的生产改动——
其转换与 run_pipeline 白盒测试捆绑（Tier-2 既定排期），届时 bat 重定向降级为
"仅收 stderr"。
"""
import logging
import sys
from pathlib import Path

from config import STORE_DIR

LOG_DIR = STORE_DIR / "logs"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_CONFIGURED: set[str] = set()


def get_logger(script_path: str | Path, logfile: bool = True) -> logging.Logger:
    """按脚本路径取 logger（名 = 文件 stem），控制台 + 可选 data/store/logs/{stem}.log。

    幂等：同脚本重复调用不叠加 handler（研究脚本互 import 时安全）。
    """
    stem = Path(script_path).stem if script_path else "root"
    logger = logging.getLogger(stem)
    if stem in _CONFIGURED:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if logfile:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_DIR / f"{stem}.log", encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    _CONFIGURED.add(stem)
    return logger
