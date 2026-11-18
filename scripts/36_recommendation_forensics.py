"""第 36 步：推荐行为法证（用户 2026-11-07 构想二"马后炮军师"的测试内核，D-068）。

三个问题，全部用现有账本 + 面板收盘价回答（离线，零 bs 依赖）：
  Q1 买入质量：每次实际买入后 5/20 日，跑赢当日全池中位数的比例与超额——
     因子打分在"真实成交"上的选择力（与 28 号随机对照互为印证）；
  Q2 卖出质量：每次实际卖出后，被卖标的的后续表现 vs 全池——
     "卖飞率"（卖了之后反而跑赢池子的比例），量化 20 日重排的退出质量；
  Q3 否决价值：全部被人工否决的单子（11-02 的 25 笔 + 10-23 的 1 笔），
     假设按原计划成交后的 5/20 日表现——回答"人工干预在加分还是减分"。
     方向约定：否决买入 = 没买 → 贡献 = -该票超额；否决卖出 = 继续持有 → 贡献 = +该票超额。

两套基准（D-087，**平行输出，原口径不动**）：
  A. 主口径 = 当日 **全池中位**前向收益（D-068 原文）。问题：防御型组合（低波/低换手）
     在市场下跌时天然超额、上涨时天然落后，所以这一列反映的很大程度是"那天大盘涨跌"，
     不是选股能力。
  B. 参照口径 = 当日 **同波动分位组的中位**前向收益（20 日已实现波动分 5 组）。
     把每笔成交改成和"同一天、同样波动水平"的股票比，剔除上面那层市场方向暴露。
  两列都保留：A 用于与 v1 历史对比，B 用于判断"选股/卖出本身准不准"。

口径：前向收益从成交日/否决日收盘起算（T 收盘→T+n 收盘，同 D-038 的 T+1 可成交
口径差一天，法证视角用成交日收盘即可）；波动分组只用 t 及之前的数据（rolling 到 t），无前视。
样本量小（百笔级）——本脚本是**描述性法证**，不做统计检验，结论以"方向+幅度"呈现。
临近面板末尾、不足 n 个交易日的订单前向收益记 None，不参与统计。
价格来源：**直接读仓库**（D-086）——旧版读 30 号研究缓存 `rotation_panel_all55.parquet`，
该文件停在 2026-11-06，前向窗口永远算不出来（11-17 复核因此误判"样本未熟"）。

用法：python scripts/36_recommendation_forensics.py
"""
import sqlite3
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.strategies.registry import enabled_accounts

PANEL_CACHE = STORE_DIR / "research_results" / "rotation_panel_all55.parquet"
OUT_DIR = STORE_DIR / "research_results"
FWD_DAYS = (5, 20)
VOL_WINDOW = 20        # 波动分组回看窗口（交易日）
N_VOL_GROUPS = 5       # 同波动分位组数（0=最低波）


def load_close_pivot() -> pd.DataFrame:
    """全池收盘价宽表 —— **直接读仓库**（2026-11-17 修）。

    原实现读 30 号研究缓存 `rotation_panel_all55.parquet` 取收盘价，而那个文件是
    2026-11-07 建的一次性产物、停在 11-06：前向收益只能在 11-06 之前成形，11-02/11-03
    的卖出与否决窗口永远算不出来——11-17 凌晨复核因此误判成"样本仍未熟（nan/n=1）"。
    法证是日更消费者，价格必须来自活的仓库。

    口径按 D-068 原文取 **PIT 并集全池**（沪深300 历史成分并集 ≈949 只）；v1 当时
    受研究缓存所限只用了 564 只，故买入质量绝对值与 v1 不可逐位对比。
    """
    from src.data import universe
    from src.data.market import load_price_matrix
    from src.data.storage import ParquetStore

    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    prices, _opens = load_price_matrix(symbols, store=store)
    return prices


def fwd_returns(pc: pd.DataFrame) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """前向收益宽表 + 每日池中位。返回 {n: fwd}, med5, med20。"""
    fwds, meds = {}, {}
    for n in FWD_DAYS:
        fwds[n] = pc.shift(-n) / pc - 1.0
        meds[n] = fwds[n].median(axis=1)
    return fwds, meds[5], meds[20]


def vol_buckets(pc: pd.DataFrame, window: int = VOL_WINDOW,
                groups: int = N_VOL_GROUPS) -> pd.DataFrame:
    """t 日、每只股票的**同波动分位组号**（0=最低波、groups-1=最高波）。

    波动 = t 及之前 window 个交易日的已实现波动（rolling，收盘到收盘），
    组号 = 当日截面波动排名的等分位。NaN（历史不足/停牌）保持 NaN → 该笔不进
    参照口径，绝不回退成"全池中位"冒充（那样会把两种口径混在一起）。
    """
    vol = pc.pct_change(fill_method=None).rolling(
        window, min_periods=max(10, window // 2)).std()
    rk = vol.rank(axis=1, pct=True)          # 每日截面百分位（0~1）
    return np.floor(rk * groups).clip(upper=groups - 1)


def group_medians(fwd: pd.DataFrame, buckets: pd.DataFrame,
                  groups: int = N_VOL_GROUPS) -> dict:
    """每天、每个波动组的前向收益中位（{组号: Series(日期 → 中位)}）。"""
    out = {}
    for g in range(groups):
        out[g] = fwd.where(buckets == g).median(axis=1)
    return out


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
    buckets = vol_buckets(pc)
    gmeds = {n: group_medians(fwds[n], buckets) for n in FWD_DAYS}   # 参照口径基准
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
        g = buckets.at[t, r.symbol] if r.symbol in buckets.columns else np.nan
        row["vol_group"] = None if pd.isna(g) else int(g)
        for n in FWD_DAYS:
            f = float(fwds[n].loc[t, r.symbol]) if r.symbol in pc.columns else None
            m = float(meds[n].loc[t]) if t in meds[n].index else None
            if f is not None and pd.notna(f) and m is not None and pd.notna(m):
                row[f"fwd{n}"] = f
                row[f"exc{n}"] = f - m
            else:
                row[f"fwd{n}"] = None
                row[f"exc{n}"] = None
            # 参照口径：同波动组中位。组号缺失 → None（不回退成全池中位）
            m_v = (float(gmeds[n][int(g)].get(t, np.nan)) if not pd.isna(g) else np.nan)
            row[f"exc{n}_vadj"] = (f - m_v
                                   if f is not None and pd.notna(f) and pd.notna(m_v)
                                   else None)
        rows.append(row)
    df = pd.DataFrame(rows)

    def block(sub: pd.DataFrame, title: str, suffix: str = "") -> None:
        """suffix="" → 全池中位基准（exc5/exc20）；suffix="_vadj" → 同波动组基准。"""
        c5, c20 = f"exc5{suffix}", f"exc20{suffix}"
        sub = sub.dropna(subset=[c5])
        if sub.empty:
            print(f"  {title}: 无有效样本")
            return
        print(f"  {title}（n={len(sub)}）")
        for label, side in (("买入", "buy"), ("卖出", "sell")):
            g = sub[sub["side"] == side]
            if not len(g):
                print(f"    {label}：无样本")
                continue
            e5 = g[c5]
            ok = (e5 > 0) if side == "buy" else (e5 < 0)
            tag = "跑赢基准" if side == "buy" else "卖对比例"
            print(f"    {label}超额(5日) 均值 {e5.mean():+.2%} | {tag} {ok.mean():.0%}（n={len(e5)}）")
            e20 = g[c20].dropna() if c20 in g.columns else pd.Series(dtype=float)
            if len(e20):
                print(f"    {label}超额(20日) 均值 {e20.mean():+.2%}（n={len(e20)}，窗口不足者不计）")

    print("\n== Q1/Q2 成交质量（基准 A：当日全池中位；D-068 原口径）==")
    for acc in df["account"].unique():
        block(df[(df["kind"] == "成交") & (df["account"] == acc)], f"[{acc}]")
    block(df[df["kind"] == "成交"], "全部账户合计")

    print("\n== Q1b/Q2b 成交质量（基准 B：同日 + 同波动组中位；D-087 新增，平行口径）==")
    print("   用途：剔除「防御型组合跌市超额、涨市落后」这层市场方向暴露，只看选股/卖出本身。")
    for acc in df["account"].unique():
        block(df[(df["kind"] == "成交") & (df["account"] == acc)], f"[{acc}]", suffix="_vadj")
    block(df[df["kind"] == "成交"], "全部账户合计", suffix="_vadj")

    print("\n== Q3 否决价值（人工否决的单子，假设成交）==")
    veto = df[df["kind"] == "否决"].copy()
    if len(veto):
        # 否决的贡献（相对"按原计划执行"）：
        #   否决买入 = 没买 → 贡献 = -该票超额（票跌了 = 否决加分）
        #   否决卖出 = 继续持有 → 贡献 = +该票超额（票涨了 = 否决加分）
        # 2026-11-17 修正：旧版把卖单方向判反了（写成"否决卖出→真跌了=躲过损失"，
        # 但否决卖出等于继续持有，真跌了是实打实的损失），结论会完全反过来。
        v = veto.dropna(subset=["exc5"]).copy()
        print(f"  有效样本 n={len(v)}（其余前向窗口不足）")
        for _, r in v.iterrows():
            d3 = "（仅3日窗口）" if r["date"] >= last_date - pd.Timedelta(days=7) else ""
            print(f"    {r['date'].date()} {r['account']:<6} {r['side']:<4} {r['symbol']} "
                  f"fwd5={r['fwd5']:+.2%} 超池 {r['exc5']:+.2%} {d3}")
        v["contrib"] = [-r.exc5 if r.side == "buy" else r.exc5 for r in v.itertuples()]
        v["amount"] = (pd.to_numeric(v["qty"], errors="coerce")
                       * pd.to_numeric(v["price"], errors="coerce"))
        for side, label in (("buy", "否决买单（没买）"), ("sell", "否决卖单（继续持有）")):
            sub = v[v["side"] == side]
            if not len(sub):
                print(f"  {label}：无样本")
                continue
            print(f"  {label} n={len(sub)}：平均贡献 {sub['contrib'].mean():+.2%}，"
                  f"事后证明否决正确 {(sub['contrib'] > 0).sum()}/{len(sub)}")
        print(f"  合计：简单平均贡献 {v['contrib'].mean():+.2%}（正=人工否决加分、负=减分）")
        if v["amount"].notna().all() and v["amount"].sum() > 0:
            print(f"        金额加权贡献 {(v['contrib'] * v['amount']).sum() / v['amount'].sum():+.2%}"
                  f"（涉及 {v['amount'].sum():,.0f} 元）")
        # 参照口径：同波动组基准下的同一笔否决（否决发生在同一天，天然免疫市场方向）
        va = v.dropna(subset=["exc5_vadj"]).copy()
        if len(va):
            va["contrib"] = [-r.exc5_vadj if r.side == "buy" else r.exc5_vadj
                             for r in va.itertuples()]
            print(f"  [基准B 同日同波动组] n={len(va)}：简单平均贡献 {va['contrib'].mean():+.2%}"
                  f"（买 {(va[va.side == 'buy']['contrib'] > 0).sum()}/"
                  f"{len(va[va.side == 'buy'])} 笔正确）")
    else:
        print("  无否决记录")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_DIR / "recommendation_forensics.parquet", index=False)
    print("\n>> 明细已存 research_results/recommendation_forensics.parquet"
          "（两套基准各一列：exc* / exc*_vadj）")


if __name__ == "__main__":
    main()
