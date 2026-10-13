# -*- coding: utf-8 -*-
"""把项目根目录加入 sys.path，使脚本可直接 `python scripts/xxx.py` 运行。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
