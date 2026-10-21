# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\MeineArbeit\QuantDex")
from src.data.storage import ParquetStore
import collections
store = ParquetStore()
reg = store.registry()
print("注册表数据集:", len(reg), "| 总行数:", int(reg["rows"].sum()))
print(reg["dataset"].value_counts().head(8).to_string())
import glob, os
from config import STORE_DIR
print("日线文件:", len(glob.glob(str(STORE_DIR/"market"/"daily"/*.parquet"))))
print("分红文件:", len(glob.glob(str(STORE_DIR/"financials"/"baostock"/*__dividend.parquet"))))
