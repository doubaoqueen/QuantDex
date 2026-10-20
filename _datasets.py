# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\MeineArbeit\QuantDex")
from src.data.storage import ParquetStore
store = ParquetStore()
for kind in ("daily", "meta", "financial", "valuation", "moneyflow", "macro", "bar"):
    try:
        n = store.list_names(kind)
        print(f"{kind}: {len(n)} 个 | 例: {n[:5]}")
    except Exception as e:
        print(f"{kind}: {type(e).__name__}: {str(e)[:80]}")
try:
    reg = store.registry()
    print("\n注册表 dataset 前缀分布:")
    print(reg["dataset"].value_counts().head(15).to_string())
except Exception as e:
    print("registry err", e)
