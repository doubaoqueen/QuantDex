# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"D:\MeineArbeit\QuantDex")
from config import STORE_DIR
from src.live.ledger import Ledger
import pandas as pd

led = Ledger(STORE_DIR / "sim" / "ledger.sqlite")
tr = led.trades()
led.close()

buy = tr[tr["side"] == "buy"]
sell = tr[tr["side"] == "sell"]
fee = float(tr["fee"].sum())
stamp = float(tr["stamp_duty"].sum())
amt = float(buy["amount"].sum())
# 滑点按 0.05% 计入现金但未单列（口径见 pipeline SLIP）
slip = float((buy["amount"].sum() + sell["amount"].sum()) * 0.0005)
print("== 大账户(100万)成交成本核对 ==")
print(f"成交笔数: {len(tr)} (买 {len(buy)} / 卖 {len(sell)})")
print(f"买入成交额合计: {amt:,.2f}")
print(f"佣金合计(账本): {fee:,.2f}")
print(f"印花税合计(账本): {stamp:,.2f}")
print(f"滑点(0.05%/边): {slip:,.2f}  [未单列，已含在现金扣减]")
print(f"过户费(0.001%/边, 未建模): {amt*0.00001:,.2f}")
print(f"最低佣金5元/笔 (未建模, 按49笔估算): {49*5:,.2f}  (实际佣金={fee:,.2f})")
print()
print("现金恒等式核对:")
cap = 1_000_000.0
snap = Ledger(STORE_DIR / "sim" / "ledger.sqlite").snapshot()
cash = float(snap[snap["date"] == "2026-10-20"]["cash"].iloc[0])
mv = float(snap[snap["date"] == "2026-10-20"]["market_value"].iloc[0])
print(f"  期初 {cap:,.2f} - 买入额 {amt:,.2f} - 佣金 {fee:,.2f} - 滑点 {slip:,.2f} = {cap-amt-fee-slip:,.2f}")
print(f"  账本现金 {cash:,.2f} -> 差额 {abs(cap-amt-fee-slip-cash):,.2f} (应≈0)")
print(f"  净值 = 现金 {cash:,.2f} + 市值 {mv:,.2f} = {cash+mv:,.2f}")
