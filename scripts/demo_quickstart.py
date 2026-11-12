"""30 秒出图（D-082）：指向仓库自带的 demo 数据包，全程离线、不碰真实数据仓库。

用法：python scripts/demo_quickstart.py [代码，默认600519]
前置：无需抓取任何数据；仅依赖 pandas / numpy / pyarrow / pillow。
产出：质检 + 收益概览 + 分年度 + 双均线回测的终端报告，3 张 PNG（demo/store/plots/）。
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# 必须在 import config 之前设置：config 在 import 时读该变量确定仓库位置（D-082）
os.environ.setdefault("QUANTDEX_STORE_DIR", str(_ROOT / "demo" / "store"))

import _bootstrap  # noqa: F401,E402
import demo_analysis  # noqa: E402

if __name__ == "__main__":
    demo_analysis.main()
