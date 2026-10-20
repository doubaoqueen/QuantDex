# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\MeineArbeit\QuantDex")
import pandas as pd
from config import STORE_DIR
from src.data.storage import ParquetStore
store = ParquetStore()
reg = store.registry()
daily = reg[reg["dataset"] == "daily"]
print("注册表数据集:", len(reg), "| 总行数:", int(reg["rows"].sum()))
print("daily 文件:", len(daily), "| daily 行数:", int(daily["rows"].sum()))
sl = store.read("meta", "stock_list")
print("股票列表:", len(sl), "只")
