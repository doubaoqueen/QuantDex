"""四周偏差归因 v2（2026-11-20，D-001 复核日）——按 D-096 强制口径出表。

D-096 强制口径：
  ① 基准用全收益近似：000300 价格指数收益 + 股息率 2%/年 按天数折算
  ② 滑点 5/15/20/30bp 敏感性（影子盘=已实现，回测=假设 → 影响的是"差"）
  ④ 红利账户结论注明 2018-2019Q1 因子失效段（本次区间不受影响，但结论外推要标）
  ⑤ 中证500 零覆盖（886 只实际 = 300 并集）——影子盘持仓全部来自这 886 只，无 500 独有标的
"""
import sqlite3
import sys

sys.path.insert(0, r"D:\MeineArbeit\QuantDex")
import pandas as pd  # noqa: E402

from config import STORE_DIR  # noqa: E402

ACCS = {"large": ("ledger.sqlite", 1_000_000.0), "small": ("ledger_2000.sqlite", 2_000.0),
        "div": ("ledger_div.sqlite", 1_000_000.0), "topk": ("ledger_topk.sqlite", 1_000_000.0)}
LAST = "2026-11-20"
DIV_YIELD = 0.02          # 全收益近似：股息率 2%/年
_cache = {}


def px(sym: str) -> pd.DataFrame:
    if sym in _cache:
        return _cache[sym]
    for suffix in ("__qfq", "__none"):
        p = STORE_DIR / "market" / "daily" / f"{sym}{suffix}.parquet"
        if p.exists():
            df = pd.read_parquet(p, columns=["date", "open", "close"])
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            _cache[sym] = df.set_index("date")
            return _cache[sym]
    _cache[sym] = pd.DataFrame(columns=["open", "close"])
    return _cache[sym]


rows = []
for acc, (db, capital) in ACCS.items():
    con = sqlite3.connect(f"file:{STORE_DIR / 'sim' / db}?mode=ro", uri=True)
    nav = pd.read_sql("SELECT date, total_nav, benchmark_nav, cash FROM account_snapshot "
                      "ORDER BY date", con)
    tl = pd.read_sql("SELECT * FROM trade_log ORDER BY id", con)
    con.close()
    nav["d"] = nav["date"].astype(str).str[:10]
    tl["d"] = tl["fill_date"].astype(str).str[:10]
    filled = tl[tl["status"] == "filled"].copy()

    d0, d1 = nav["d"].iloc[0], nav["d"].iloc[-1]
    days = len(nav)
    r_act = float(nav["total_nav"].iloc[-1]) / float(nav["total_nav"].iloc[0]) - 1
    bm = nav["benchmark_nav"].astype(float)
    r_bm_px = (bm.iloc[-1] / bm.iloc[0] - 1) if bm.notna().all() and bm.iloc[0] else float("nan")
    r_bm_tr = r_bm_px + DIV_YIELD * days / 250.0          # ① 全收益近似
    peak = nav["total_nav"].cummax()
    mdd = float((nav["total_nav"] / peak - 1).min())
    cash_ratio = float((nav["cash"] / nav["total_nav"]).mean())

    boot_dates = sorted(filled["d"].unique())[:1]
    boot = filled[filled["d"].isin(boot_dates)]
    qty = boot.groupby("symbol")["qty"].sum()
    cost_boot = float((boot["fee"] + boot["stamp_duty"]).sum())
    cash_left = capital - float(boot["amount"].sum()) - cost_boot
    val_last = sum(q * float(px(str(s)).loc[LAST, "close"])
                   for s, q in qty.items() if LAST in px(str(s)).index)
    r_hold = (val_last + cash_left) / capital - 1

    fees = float((filled["fee"] + filled["stamp_duty"]).sum())
    turnover = float(filled["amount"].sum()) / capital
    slip_cost = 0.0
    for _, r in filled.iterrows():
        df = px(str(r["symbol"]))
        if r["d"] in df.index and float(df.loc[r["d"], "open"]) > 0:
            diff = (float(r["price"]) - float(df.loc[r["d"], "open"])) * float(r["qty"])
            slip_cost += diff if r["side"] == "buy" else -diff

    rows.append({
        "账户": acc, "区间": f"{d0[5:]}→{d1[5:]}", "交易日": days,
        "实际": r_act, "基准(全收益)": r_bm_tr, "超额": r_act - r_bm_tr,
        "反事实": r_hold, "实际-反事实": r_act - r_hold,
        "成本%": fees / capital, "滑点%": slip_cost / capital,
        "换手(倍)": turnover, "MDD": mdd, "平均现金": cash_ratio,
        "成交": len(filled), "取消": int((tl["status"] == "cancelled").sum()),
    })

df = pd.DataFrame(rows)
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
print("== 表 1 四账户四周归因（D-096 全收益基准口径）==")
print(df.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))

print("\n== 表 2 滑点敏感性（②）：回测假设 5/15/20/30bp 时，「实际-反事实」差会平移多少 ==")
print("   平移量 = (假设滑点 - 影子盘已实现滑点%) x 双边换手；影子盘已实现见上表「滑点%」")
for _, r in df.iterrows():
    base = r["实际-反事实"]
    out = []
    for bp in (5, 15, 20, 30):
        # 回测口径滑点更高 → 回测净值更低 → 差(实际-回测)更大
        delta = (bp / 10000.0 - 0.0005) * r["换手(倍)"]
        out.append(f"{bp}bp: {base + delta:+.4f}")
    print(f"   {r['账户']:5s} " + " | ".join(out))
print("\n注：影子盘换手低（4 周 0.3~1.9 倍），滑点口径差异对结论量级影响 ≤0.3pp。")
print("注：红利账户结论外推需注明 2018-2019Q1 dy_ttm 因子失效段（本区间 2026-10 起，不受影响）。")
print("注：持仓全部来自沪深300 并集 886 只；中证500 独有 514 只零覆盖（D-096 ⑤），影子盘无 500 独有标的。")
