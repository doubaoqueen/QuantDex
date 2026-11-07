"""第 36 步：推荐行为法证（用户 2026-11-07 构想二"马后炮军师"的测试内核，D-068）。

三个问题，全部用现有账本 + 面板收盘价回答（离线，零 bs 依赖）：
  Q1 买入质量：每次实际买入后 5/20 日，跑赢当日全池中位数的比例与超额——
     因子打分在"真实成交"上的选择力（与 28 号随机对照互为印证）；
  Q2 卖出质量：每次实际卖出后，被卖标的的后续表现 vs 全池——
     "卖飞率"（卖了之后反而跑赢池子的比例），量化 20 日重排的退出质量；
  Q3 否决价值：全部被人工否决的单子（11-02 的 25 笔 + 10-23 的 1 笔），
     假设按原计划成交后的 5/20 日表现——回答"人工干预在加分还是减分"。

口径：前向收益从成交日/否决日收盘起算（T 收盘→T+n 收盘，同 D-038 的 T+1 可成交
口径差一天，法证视角用成交日收盘即可）；对比基准 = 当日 PIT 池中位前向收益。
样本量小（百笔级）——本脚本是**描述性法证**，不做统计检验，结论以"方向+幅度"呈现。
11-03 成交的订单前向窗口不足，单独标注（fwd3，截至 2026-11-06）。

用法：python scripts/36_recommendation_forensics.py
"""
import sqlite3
from pathlib import Path

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.strategies.registry import enabled_accounts

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "research_results"
FWD_DAYS = (5, 20)


def load_close_pivot() -> pd.DataFrame:
    panel = pd.read_parquet(PANEL_CACHE, columns=["date", "symbol", "close"])
    panel["date"] = pd.to_datetime(panel["date"])
    return panel.pivot(index="date", columns="symbol", values="close").sort_index()


def fwd_returns(pc: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """前向收益宽表 + 每日池中位。返回 {n: fwd}, med5, med20。"""
    fwds, meds = {}, {}
    for n in FWD_DAYS:
        fwds[n] = pc.shift(-n) / pc - 1.0
        meds[n] = fwds[n].median(axis=1)
    return fwds, meds[5], meds[20]


def collect_trades() -> pd.DataFrame:
    """四账本全部 filled + cancelled 订单，打标签（账户/类型）。"""
    rows = []
    for key, cfg in enabled_accounts().items():
        db = STORE_DIR / "sim" / cfg["ledger"]
        if not db.exists():
            continue
        c = sqlite3.connect(db)
        t = pd.read_sql_query(
            "SELECT signal_date, fill_date, symbol, side, qty, price, status "
            "FROM trade_log WHERE status IN ('filled','cancelled')", c)
        c.close()
        t["account"] = key
        rows.append(t)
    t = pd.concat(rows, ignore_index=True)
    t["fill_date"] = pd.to_datetime(t["fill_date"])
    return t


def main() -> None:
    pc = load_close_pivot()
    fwds, med5, med20 = fwd_returns(pc)
    meds = {5: med5, 20: med20}
    last_date = pc.index[-1]
    trades = collect_trades()
    trades["symbol"] = trades["symbol"].astype(str)

    rows = []
    for r in trades.itertuples(index=False):
        t = pd.Timestamp(r.fill_date)
        if t not in pc.index:
            continue
        kind = ("成交" if r.status == "filled"
                else "否决" if r.status == "cancelled" else r.status)
        # 前向窗口：成交/否决日收盘起算；窗口不足的（临近面板末尾）标 None
        row = {"account": r.account, "kind": kind, "side": r.side,
               "symbol": r.symbol, "date": t, "qty": r.qty, "price": r.price}
        for n in FWD_DAYS:
            f = float(fwds[n].loc[t, r.symbol]) if r.symbol in pc.columns else None
            m = float(meds[n].loc[t]) if t in meds[n].index else None
            if f is not None and pd.notna(f) and m is not None and pd.notna(m):
                row[f"fwd{n}"] = f
                row[f"exc{n}"] = f - m
            else:
                row[f"fwd{n}"] = None
                row[f"exc{n}"] = None
        rows.append(row)
    df = pd.DataFrame(rows)

    def block(sub: pd.DataFrame, title: str, n: int) -> None:
        sub = sub.dropna(subset=[f"exc{n}"])
        if sub.empty:
            print(f"  {title}: 无有效样本")
            return
        # 买方超额>0=好；卖方超额<0=好（卖出标的比池子弱=卖对了）
        hit = ((sub["exc5"] > 0) & (sub["side"] == "buy")).sum() / max((sub["side"] == "buy").sum(), 1)
        sell_ok = ((sub["exc5"] < 0) & (sub["side"] == "sell")).sum() / max((sub["side"] == "sell").sum(), 1)
        print(f"  {title}（n={len(sub)}）")
        print(f"    买入超额(5日) 均值 {sub[sub['side']=='buy']['exc5'].mean():+.2%} | "
              f"跑赢池比例 {hit:.0%}")
        print(f"    卖出超额(5日) 均值 {sub[sub['side']=='sell']['exc5'].mean():+.2%} | "
              f"卖对比例 {sell_ok:.0%}")
        try:
            e20b = sub[(sub["side"] == "buy")]["exc20"].dropna()
            e20s = sub[(sub["side"] == "sell")]["exc20"].dropna()
            if len(e20b):
                print(f"    买入超额(20日) 均值 {e20b.mean():+.2%}")
            if len(e20s):
                print(f"    卖出超额(20日) 均值 {e20s.mean():+.2%}")
        except KeyError:
            pass

    print("\n== Q1/Q2 成交质量（全部实际成交，按账户）==")
    for acc in df["account"].unique():
        block(df[(df["kind"] == "成交") & (df["account"] == acc)], f"[{acc}]", 5)
    block(df[df["kind"] == "成交"], "全部账户合计", 5)

    print("\n== Q3 否决价值（人工否决的单子，假设成交）==")
    veto = df[df["kind"] == "否决"].copy()
    if len(veto):
        # 买方：否决买入 → 若该票后续上涨，否决损失了收益；卖方：否决卖出 → 若该票后续下跌，否决躲过了损失
        v = veto.dropna(subset=["exc5"])
        print(f"  有效样本 n={len(v)}（其余前向窗口不足）")
        for _, r in v.iterrows():
            d3 = "（仅3日窗口）" if r["date"] >= last_date - pd.Timedelta(days=7) else ""
            print(f"    {r['date'].date()} {r['account']:<6} {r['side']:<4} {r['symbol']} "
                  f"fwd5={r['fwd5']:+.2%} 超池 {r['exc5']:+.2%} {d3}")
        buy_lost = v[(v["side"] == "buy") & (v["exc5"] > 0)]
        sell_saved = v[(v["side"] == "sell") & (v["exc5"] < 0)]
        print(f"  小结：否决买单中事后后悔（真涨了）{len(buy_lost)}/{len(v[v['side']=='buy'])}；"
              f"否决卖单中事后正确（真跌了）{len(sell_saved)}/{len(v[v['side']=='sell'])}")
    else:
        print("  无否决记录")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / "recommendation_forensics.parquet", index=False)
    print("\n>> 明细已存 research_results/recommendation_forensics.parquet")


if __name__ == "__main__":
    main()
