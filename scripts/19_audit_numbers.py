"""
数字审计：账户/持仓/成交/报表逐项对账，输出 PASS/FAIL。

检查项：
  1. 净值恒等式：nav = cash + market_value
  2. 现金连续性：cash_t = cash_{t-1} + 卖出净额 - 买入总额 - 滑点
  3. 成交金额：amount ≈ qty × price
  4. 持仓权重：weight ≈ shares × close / nav
  5. 每日操作记录：每个 (date, account, action) 最多 1 行
  6. 报表一致性：最新日报的净值/今日盈亏/累计收益 与账本一致
用法：python scripts/19_audit_numbers.py
"""
import re
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.live.ledger import Ledger

SLIP = 0.0005
from src.strategies.registry import enabled_accounts

# R3-29：审计覆盖全部注册表账户（div/topk 不再漏审）
ACCOUNTS = {k: STORE_DIR / "sim" / c["ledger"] for k, c in enabled_accounts().items()}
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def audit(acc, path):
    if not path.exists():
        check(f"[{acc}] 账本存在", False)
        return
    led = Ledger(path)
    snap = led.snapshot().sort_values("date")
    trades = led.trades()
    ops = led.daily_ops(acc, limit=1000)
    led.close()

    # 1) 净值恒等式
    bad = snap[(snap["total_nav"] - snap["cash"] - snap["market_value"]).abs() > 0.01]
    check(f"[{acc}] 净值 = 现金 + 市值", len(bad) == 0,
          f"{len(bad)} 行不符" if len(bad) else f"{len(snap)} 行全部一致")

    # 2) 现金连续性
    worst = 0.0
    worst_day = ""
    for i in range(1, len(snap)):
        d0, d1 = str(snap["date"].iloc[i - 1]), str(snap["date"].iloc[i])
        fills = trades[(trades["status"] == "filled")
                       & (trades["fill_date"] > d0) & (trades["fill_date"] <= d1)]
        delta = 0.0
        for t in fills.itertuples(index=False):
            amt = float(t.amount)
            fee = float(t.fee or 0) + amt * SLIP
            stamp = float(t.stamp_duty or 0)
            delta += (-amt - fee) if t.side == "buy" else (amt - fee - stamp)
        expected = float(snap["cash"].iloc[i - 1]) + delta
        diff = abs(float(snap["cash"].iloc[i]) - expected)
        if diff > worst:
            worst, worst_day = diff, d1
    check(f"[{acc}] 现金连续性", worst < 1.0,
          f"最大偏差 {worst:,.2f} 元（{worst_day}）")

    # 3) 成交金额
    bad_amt = trades[(trades["amount"] - trades["qty"] * trades["price"]).abs() > 0.01]
    check(f"[{acc}] 成交金额 = 数量 × 价格", len(bad_amt) == 0,
          f"{len(bad_amt)} 笔不符" if len(bad_amt) else f"{len(trades)} 笔一致")

    # 4) 持仓权重
    led = Ledger(path)
    last = led.positions().sort_values("date")
    led.close()
    if len(last):
        d = str(last["date"].iloc[-1])
        nav = float(snap[snap["date"] == d]["total_nav"].iloc[0]) if (snap["date"] == d).any() else None
        pos = last[last["date"] == d]
        if nav:
            bad_w = pos[(pos["weight"] - pos["close"] * pos["shares"] / nav).abs() > 1e-6]
            check(f"[{acc}] 持仓权重 = 市值 / 净值", len(bad_w) == 0,
                  f"{len(bad_w)} 行不符" if len(bad_w) else f"{len(pos)} 行一致")

    # 5) 每日操作记录去重
    if len(ops):
        dup = ops.duplicated(subset=["date", "action"]).sum()
        check(f"[{acc}] 每日操作记录一天一条", int(dup) == 0, f"{len(ops)} 条记录")

    # 6) 报表一致性（最新日报）
    rep = STORE_DIR / "reports" / acc / f"{snap['date'].iloc[-1]}.md"
    if rep.exists() and len(snap):
        txt = rep.read_text(encoding="utf-8")
        m_nav = re.search(r"总净值: ([\d,\.]+)", txt)
        m_pnl = re.search(r"今日盈亏: ([-+]?[\d,\.]+)", txt)
        m_cum = re.search(r"累计收益: ([-+]?[\d\.]+)%", txt)
        nav_ok = abs(float(m_nav.group(1).replace(",", "")) - float(snap["total_nav"].iloc[-1])) < 0.01 if m_nav else False
        prior = snap[pd.to_datetime(snap["date"]) < pd.Timestamp(snap["date"].iloc[-1])]
        if len(prior):
            exp_pnl = float(snap["total_nav"].iloc[-1]) - float(prior["total_nav"].iloc[-1])
            pnl_ok = abs(float(m_pnl.group(1).replace(",", "")) - exp_pnl) < 0.02 if m_pnl else False
        else:
            pnl_ok = True
        base = float(snap["total_nav"].iloc[0])
        exp_cum = (float(snap["total_nav"].iloc[-1]) / base - 1) * 100
        cum_ok = abs(float(m_cum.group(1)) - exp_cum) < 0.05 if m_cum else False
        check(f"[{acc}] 日报数字与账本一致", nav_ok and pnl_ok and cum_ok,
              f"净值{nav_ok} 今日盈亏{pnl_ok} 累计{cum_ok}")
    else:
        check(f"[{acc}] 日报存在", False, str(rep))


def main():
    for acc, path in ACCOUNTS.items():
        audit(acc, path)
    print("== 数字审计 ==")
    fails = 0
    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))
        fails += 0 if ok else 1
    print(f"\n合计: {len(results)} 项，失败 {fails} 项")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
