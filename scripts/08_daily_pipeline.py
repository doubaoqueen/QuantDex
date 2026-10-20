# -*- coding: utf-8 -*-
"""
第 8 步：影子盘每日流水线（收盘后运行）。

流程：读本地数据 -> 目标权重(经风控) -> 模拟成交(收盘价) -> 写账本(快照/持仓/成交)
      -> 生成纯文本日报 data/store/reports/YYYY-MM-DD.md

用法：
    python scripts/08_daily_pipeline.py                      # 正常运行（首次运行自动建仓）
    python scripts/08_daily_pipeline.py --dry-run            # 只计算不落账本
    python scripts/08_daily_pipeline.py --top-n 50 --rebalance 20 --capital 1000000
注意：数据更新请先跑 02/04（本脚本不联网）。
"""
import sys

import _bootstrap  # noqa: F401

from src.live.pipeline import run_pipeline


def _arg(name: str, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return type(default)(sys.argv[i + 1])
    return default


def _str_arg(name: str, default: str | None = None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> None:
    run_pipeline(
        top_n=_arg("--top-n", 50),
        rebalance=_arg("--rebalance", 20),
        capital=_arg("--capital", 1_000_000.0),
        dry_run="--dry-run" in sys.argv,
        push="--no-push" not in sys.argv,   # 配置了 SMTP 时默认发送邮件
        db_path=_str_arg("--db"),
        label=_str_arg("--label", "large"),
        max_positions=int(_str_arg("--max-positions", 0)) or None,
    )


if __name__ == "__main__":
    main()
