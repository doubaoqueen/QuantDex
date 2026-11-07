"""
影子盘每日流水线（v2：T+1 两段式成交，与回测口径一致）。

每个交易日收盘后运行一次：
  1. 撮合：把 fill_date ≤ 最新数据日 的待成交订单按**次日开盘价**成交
       涨停买不进 → rejected_limit_up；跌停卖不出 → rejected_limit_down；
       停牌/零成交 → rejected_suspended；
  2. 快照：撮合后按最新收盘价记账户净值与持仓；
  3. 下单：若当天是策略调仓日（或账户首次运行），按目标权重生成新订单，
       记为 status='pending'，fill_date=下一交易日，等待次日开盘撮合；
  4. 日报：data/store/reports/YYYY-MM-DD.md。

幂等：同一天重复运行不会重复下单或撮合；快照/持仓按日期覆盖。
数据更新不在本模块（先跑 02/04）。

用法：
    python scripts/08_daily_pipeline.py              # 正常运行
    python scripts/08_daily_pipeline.py --dry-run    # 只计算不落账本
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import LOT, MIN_FEE, SLIP_RATE, STAMP_RATE, STORE_DIR, TRANSFER_RATE, board_limit
from src.analysis.factors import build_factor_panel
from src.data import financials, universe
from src.data.market import get_kline
from src.data.storage import ParquetStore
from src.live.ledger import Ledger
from src.risk.risk import apply_risk
from src.strategies.multi_factor import (
    topk_dropout_topn,
    turnover_lowvol_dividend_topn,
    turnover_lowvol_reversal_topn,
)

# 成本与撮合常量统一来自 config（D-044）
FEE = 0.00025
SLIP = SLIP_RATE
STAMP = STAMP_RATE
TRANSFER = TRANSFER_RATE


def _trade_fee(amount: float) -> float:
    """单笔交易费用 = max(成交额×佣金率, 最低佣金) + 成交额×过户费。"""
    return max(amount * FEE, MIN_FEE) + amount * TRANSFER


def _board_limit(symbol: str) -> float:
    return board_limit(symbol)


# ---------------------------------------------------------------- 数据准备
def _load_panel(store: ParquetStore, index_code: str,
                with_dividend: bool = False) -> pd.DataFrame:
    pit = universe.load_pit_constituents(index_code, store)
    symbols = universe.pit_union_symbols(index_code, store)
    div = None
    if with_dividend:
        try:
            div = financials.load_dividend_panel(store, symbols)
        except Exception as e:  # noqa: BLE001
            print(f"[dividend] 分红面板加载失败，dy_ttm 将全为空: {e}")
    return build_factor_panel(store, symbols, start="20180101",
                              pit_constituents=pit, dividend_panel=div)


def _load_state(led: Ledger) -> tuple[float, dict[str, float], str | None]:
    snap = led.snapshot()
    if snap.empty:
        return 0.0, {}, None
    last_date = str(snap["date"].iloc[-1])
    cash = float(snap["cash"].iloc[-1])
    pos = led.positions(last_date)
    shares = {str(r.symbol): float(r.shares) for r in pos.itertuples(index=False)}
    return cash, shares, last_date


def _held_trading_days(calendar, last_fill, latest) -> int | None:
    """上次成交日到 latest 之间的交易日数（含 latest、不含成交当日）。

    D-057：账户的调仓节奏按这个数字判断，而不是看"今天是不是全局调仓日"——
    建仓日晚于排程时，两者会打架（账户只持有几天就被再换一遍）。
    """
    if last_fill is None:
        return None
    cal = pd.DatetimeIndex(sorted(pd.to_datetime(calendar).unique()))
    return int(((cal > pd.Timestamp(last_fill)) & (cal <= pd.Timestamp(latest))).sum())


def _next_trading_day(store: ParquetStore, date: pd.Timestamp):
    try:
        cal = pd.to_datetime(store.read("meta", "trade_calendar")["date"])
    except FileNotFoundError:
        return None
    later = cal[cal > date]
    return later.min() if len(later) else None


def _day_prices(store: ParquetStore, date: pd.Timestamp,
                symbols) -> dict[str, dict | None]:
    """读某交易日各标的 open/close/prev_close；停牌/无行情返回 None。"""
    out: dict[str, dict | None] = {}
    for sym in symbols:
        sym = str(sym)
        try:
            df = get_kline(sym, adjust="qfq", store=store)
        except FileNotFoundError:
            out[sym] = None
            continue
        d = df[df["date"] == date]
        if d.empty or float(d["volume"].iloc[0]) <= 0:
            out[sym] = None
            continue
        prev = df[df["date"] < date]
        out[sym] = {
            "open": float(d["open"].iloc[0]),
            "close": float(d["close"].iloc[0]),
            "prev_close": float(prev["close"].iloc[-1]) if len(prev) else None,
        }
    return out


def _replay_state(led, upto_date):
    """重建截至 upto_date 的（cash, shares）：上期快照 + 未入快照的成交重放。"""
    snap = led.snapshot()
    if snap.empty:
        return None
    snap["date"] = pd.to_datetime(snap["date"])
    prior = snap[snap["date"] <= pd.Timestamp(upto_date)]
    if prior.empty:
        return None
    base = prior.iloc[-1]
    cash = float(base["cash"])
    shares = {}
    pos = led.positions(str(base["date"]))
    for r in pos.itertuples(index=False):
        shares[str(r.symbol)] = float(r.shares)
    fills = led.unreconciled_fills(str(base["date"]))
    if not fills.empty:
        fills = fills[pd.to_datetime(fills["fill_date"]) <= pd.Timestamp(upto_date)]
    for t in fills.itertuples(index=False):
        q = float(t.qty) if t.qty else (
            float(t.amount) / float(t.price) if t.price else 0.0)
        if str(t.side) == "buy":
            cash -= float(t.amount) + float(t.fee) + float(t.amount) * SLIP
            shares[str(t.symbol)] = shares.get(str(t.symbol), 0.0) + q
        else:
            cash += float(t.amount) - float(t.fee) - float(t.stamp_duty) \
                - float(t.amount) * SLIP
            shares[str(t.symbol)] = max(0.0, shares.get(str(t.symbol), 0.0) - q)
    return {"cash": cash, "shares": shares}


# ---------------------------------------------------------------- 撮合
def _replay_fills(led, last_snap_date, latest, done_ids) -> pd.DataFrame:
    """需要自愈重放的成交：上一快照之后、不晚于 latest、且**不在本次已撮合集合**里。

    D-053 事故：早先只按日期过滤（fill_date > last_snap_date），而当天刚撮合的成交
    永远满足"fill_date 是今天、上一快照是昨天"，于是同一批成交被"撮合"与"重放"
    各记一遍——换手账户 49 只持仓全部变成 2 倍、现金 -87 万，而净值只差 1%。
    done_ids 就是本次撮合结果的 trade id 集合。
    """
    if last_snap_date is None:
        return pd.DataFrame()
    unre = led.unreconciled_fills(last_snap_date)
    if unre.empty:
        return unre
    unre = unre[pd.to_datetime(unre["fill_date"]) <= pd.Timestamp(latest)]
    return unre[~unre["id"].isin(done_ids)]


def _self_check(led, cash: float, positions: pd.DataFrame) -> tuple[list[str], bool]:
    """账本自检：现金非负 / 股数与成交净额一致 / 权重合计不超 100%。

    这三条正是 D-053 事故的三个指纹（当时现金 -869,765、股数全部 2 倍、
    权重合计 185%，唯独净值看起来正常），所以写成日报里的常驻检查行。
    返回 (日报行, 是否全部通过)：D-059 起失败会传导到邮件标题前缀与
    10_daily_run 的退出码——异常必须"有人响应"，不能只躺在日报文本里。
    """
    lines = ["", "## 账本自检"]
    ok = True
    if cash < -1e-6:
        lines.append(f"- ✗ 现金为负 {cash:,.2f}（现金账户不可能为负，疑似重复记账）")
        ok = False
    else:
        lines.append(f"- ✓ 现金非负 {cash:,.2f}")
    try:
        tl = pd.read_sql_query(
            "SELECT symbol, side, qty FROM trade_log WHERE status='filled'", led.connect())
        sign = tl["side"].map({"buy": 1.0, "sell": -1.0}).fillna(0.0)
        net = (pd.to_numeric(tl["qty"], errors="coerce").fillna(0.0) * sign).groupby(
            tl["symbol"].astype(str)).sum()
        pos = (positions.set_index("symbol")["shares"].astype(float)
               if len(positions) else pd.Series(dtype=float))
        diff = (pos - net.reindex(pos.index).fillna(0.0)).abs()
        bad = diff[diff > 1e-6]
        if len(bad):
            sample = ", ".join(f"{s} {pos[s]:.0f} vs {net.get(s, 0.0):.0f}" for s in bad.index[:3])
            lines.append(f"- ✗ 股数与成交净额不一致 {len(bad)} 只（{sample}）")
            ok = False
        else:
            lines.append(f"- ✓ 股数与成交净额一致（{len(pos)} 只）")
    except Exception as e:  # noqa: BLE001
        lines.append(f"- ? 股数核对跳过（{e}）")
    try:
        wsum = float(positions["weight"].sum()) if len(positions) else 0.0
        if wsum > 1.02:
            lines.append(f"- ✗ 权重合计 {wsum:.1%}，超过 100%（疑似超额配置）")
            ok = False
        else:
            lines.append(f"- ✓ 权重合计 {wsum:.1%}")
    except Exception:  # noqa: BLE001
        pass
    # D-062 第四项：滞留待成交（成交日已过 30 天仍 pending）——退市/数据长缺会
    # 卡死下单链路（pending 非空 → 不再下新单），F-23 的预警哨兵
    try:
        pend = led.pending_orders()
        stale = (pend[pd.to_datetime(pend["fill_date"])
                      < pd.Timestamp.now().normalize() - pd.Timedelta(days=30)]
                 if len(pend) else pend)
        if len(stale):
            sample = ", ".join(f"{r.symbol}@{r.fill_date}"
                               for r in stale.head(3).itertuples(index=False))
            lines.append(f"- ✗ 滞留待成交 {len(stale)} 笔（{sample}）——疑似退市/数据洞，"
                         f"会卡死下单链路")
            ok = False
        else:
            lines.append(f"- ✓ 无滞留待成交（在途 {len(pend)} 笔）")
    except Exception as e:  # noqa: BLE001
        lines.append(f"- ? 滞留订单核对跳过（{e}）")
    if ok:
        lines.append("> 自检通过。前四项对应 D-053 三指纹 + D-062 滞留订单："
                     "负现金 / 股数翻倍 / 权重>100% / pending 卡死")
    return lines, ok


def _fill_pending(led: Ledger, price_lookup: dict,
                  latest: pd.Timestamp,
                  last_dates: dict | None = None) -> list[dict]:
    """撮合 fill_date ≤ latest 的待成交订单；返回每单处理结果。

    D-038：买入前做现金硬校验（以最新快照现金为起点、随成交滚动）——
    高开时按可负担股数**部分成交**（整手递减），一手都买不起则拒单
    rejected_insufficient_cash；卖出不受限。
    """
    pend = led.pending_orders()
    if pend.empty:
        return []
    snap = led.snapshot()
    cash_run = float(snap["cash"].iloc[-1]) if not snap.empty else None
    results = []
    for o in pend.itertuples(index=False):
        fd = pd.Timestamp(o.fill_date)
        if fd > latest:
            results.append({"id": o.id, "symbol": o.symbol, "status": "pending",
                            "msg": f"未到成交日({fd.date()})"})
            continue
        dp = price_lookup.get(fd, {}).get(str(o.symbol))
        if dp is None:
            last = (last_dates or {}).get(str(o.symbol))
            if last is None or pd.Timestamp(last) < fd:
                # 行情尚未更新到成交日：保持待成交（不是停牌）。
                # D-062：但成交日已过 30 个自然日仍无行情 = 大概率退市/数据长期
                # 缺失——永远 pending 会卡死下单链路（pending 非空 → 不再下新单，
                # 账户无声停摆），超期拒单解锁并在日报自检可见。
                if (latest - fd).days > 30:
                    led.mark_rejected(o.id, "rejected_delisted",
                                      f"成交日 {fd.date()} 已过 30 天无行情（疑似退市）")
                    results.append({"id": o.id, "symbol": o.symbol,
                                    "status": "rejected_delisted",
                                    "msg": "超 30 天无行情，疑似退市拒单"})
                    continue
                results.append({"id": o.id, "symbol": o.symbol, "status": "pending",
                                "msg": f"行情未更新到 {fd.date()}，保持待成交"})
                continue
            led.mark_rejected(o.id, "rejected_suspended", "停牌/无行情")
            results.append({"id": o.id, "symbol": o.symbol,
                            "status": "rejected_suspended", "msg": "停牌/无行情"})
            continue
        prev, op = dp["prev_close"], dp["open"]
        pct = (op / prev - 1.0) if prev else 0.0
        lim = _board_limit(o.symbol)
        if o.side == "buy" and pct >= lim - 1e-3:
            led.mark_rejected(o.id, "rejected_limit_up", f"开盘涨停 {pct:.1%}")
            results.append({"id": o.id, "symbol": o.symbol,
                            "status": "rejected_limit_up", "msg": f"开盘涨停 {pct:.1%}"})
            continue
        if o.side == "sell" and pct <= -lim + 1e-3:
            led.mark_rejected(o.id, "rejected_limit_down", f"开盘跌停 {pct:.1%}")
            results.append({"id": o.id, "symbol": o.symbol,
                            "status": "rejected_limit_down", "msg": f"开盘跌停 {pct:.1%}"})
            continue
        qty = float(o.qty)
        amount = qty * op
        fee = _trade_fee(amount)
        stamp = amount * STAMP if o.side == "sell" else 0.0
        slip = amount * SLIP
        msg = "开盘价成交"
        if o.side == "buy" and cash_run is not None:
            need = amount + fee + slip
            if need > cash_run + 1e-6:
                # 现金不足：整手递减至可负担；一手都买不起 -> 拒单
                q2 = int(qty // LOT) * LOT
                while q2 >= LOT:
                    a2 = q2 * op
                    f2 = _trade_fee(a2)
                    if a2 + f2 + a2 * SLIP <= cash_run + 1e-6:
                        break
                    q2 -= LOT
                if q2 < LOT:
                    led.mark_rejected(o.id, "rejected_insufficient_cash",
                                      f"开盘价现金不足: 需{need:,.0f} 可用{cash_run:,.0f}")
                    results.append({"id": o.id, "symbol": o.symbol,
                                    "status": "rejected_insufficient_cash",
                                    "msg": f"现金不足（可用 {cash_run:,.0f}）"})
                    continue
                qty, amount = float(q2), float(q2) * op
                fee = _trade_fee(amount)
                stamp = 0.0
                slip = amount * SLIP
                msg = f"开盘价现金不足，部分成交 {qty:.0f} 股"
        led.mark_filled(o.id, str(fd.date()), op, amount, fee, stamp, qty=qty)
        if cash_run is not None:
            cash_run += (amount - fee - stamp - slip if o.side == "sell"
                         else -(amount + fee + slip))
        results.append({"id": o.id, "symbol": o.symbol, "side": o.side,
                        "status": "filled", "qty": qty, "price": op,
                        "amount": amount, "fee": fee, "stamp": stamp,
                        "slip": slip, "msg": msg})
    return results


# ---------------------------------------------------------------- 下单计划
def _plan_orders(target_w: dict[str, float], cur_shares: dict[str, float],
                 prices: dict[str, float], nav: float, cash: float) -> list[dict]:
    """生成订单（先卖后买、100 股整手、现金不足按比例缩单）。"""
    orders: list[dict] = []
    for sym, cur in cur_shares.items():
        close = prices.get(sym)
        if close is None or close <= 0:
            continue
        tgt_sh = np.floor(target_w.get(sym, 0.0) * nav / (close * LOT)) * LOT
        delta = tgt_sh - cur
        if delta < 0:
            qty = -delta
            amount = qty * close
            orders.append({"side": "sell", "symbol": sym, "qty": qty,
                           "price": close, "amount": amount,
                           "fee": _trade_fee(amount), "stamp": amount * STAMP})
            cash += amount - _trade_fee(amount) - amount * STAMP - amount * SLIP
    buys: list[dict] = []
    for sym, w in target_w.items():
        close = prices.get(sym)
        if close is None or close <= 0:
            continue
        tgt_sh = np.floor(w * nav / (close * LOT)) * LOT
        delta = tgt_sh - cur_shares.get(sym, 0.0)
        if delta > 0:
            amount = delta * close
            buys.append({"side": "buy", "symbol": sym, "qty": delta,
                         "price": close, "amount": amount,
                         "fee": _trade_fee(amount), "stamp": 0.0})
    need = sum(b["amount"] + b["fee"] + b["amount"] * SLIP for b in buys)
    if need > cash and need > 0:
        scale = cash / need
        for b in buys:
            b["qty"] = float(np.floor(b["qty"] * scale / LOT) * LOT)
        buys = [b for b in buys if b["qty"] > 0]
        for b in buys:
            b["amount"] = b["qty"] * b["price"]
            b["fee"] = _trade_fee(b["amount"])
    for b in buys:
        cash -= b["amount"] + b["fee"] + b["amount"] * SLIP
        orders.append(b)
    return orders


def _ranked_candidates(day: pd.DataFrame) -> pd.DataFrame:
    """按策略分数排序当日候选（与 multi_factor 的合成分一致）。"""
    d = day[["symbol", "turnover_20", "vol_20", "rev_5"]].dropna().copy()
    d["score"] = ((1.0 - d["turnover_20"]) + (1.0 - d["vol_20"]) + (1.0 - d["rev_5"])) / 3.0
    d["symbol"] = d["symbol"].astype(str)
    return d.sort_values("score", ascending=False)


def _plan_small_orders(day: pd.DataFrame, cur_shares: dict[str, float],
                       prices: dict[str, float], cash: float,
                       max_positions: int = 3) -> list[dict]:
    """小资金模式：按分数顺序挑「买得起 1 手」的标的，每只 1 手，最多 N 只。

    2000 元级别资金无法做 50 只等权（每只 40 元买不起 1 手），
    因此改用整手小组合；未被选中的持仓全部卖出。

    D-052 修复：预算必须是**总权益**（现金 + 现有持仓市值），而不是剩余现金。
    早先只用 cash，持仓把现金花光后新目标只能挑到最便宜的 1 手，
    于是每期"卖光 3 只、只买 1 只便宜票"，账户被逐步清空。
    """
    cand = _ranked_candidates(day)
    targets: list[str] = []
    held_value = 0.0
    for sym, sh in cur_shares.items():
        if sh > 0:
            px = prices.get(str(sym))
            if px is not None and px > 0:
                held_value += float(sh) * float(px)
    budget = float(cash) + held_value
    for r in cand.itertuples(index=False):
        px = prices.get(str(r.symbol))
        if px is None or px <= 0:
            continue
        one_lot = px * LOT
        cost = one_lot + _trade_fee(one_lot) + one_lot * SLIP
        if cost > budget:
            continue
        targets.append(str(r.symbol))
        budget -= cost
        if len(targets) >= max_positions:
            break

    orders: list[dict] = []
    for sym, sh in cur_shares.items():
        if sh > 0 and sym not in targets:
            px = prices.get(sym)
            if px is None or px <= 0:
                continue
            amount = sh * px
            orders.append({"side": "sell", "symbol": sym, "qty": sh,
                           "price": px, "amount": amount,
                           "fee": _trade_fee(amount), "stamp": amount * STAMP})
    for sym in targets:
        if cur_shares.get(sym, 0.0) > 0:
            continue
        px = prices[sym]
        amount = px * LOT
        # 预算已在选目标时预留并校验，这里直接下单
        orders.append({"side": "buy", "symbol": sym, "qty": float(LOT),
                       "price": px, "amount": amount,
                       "fee": _trade_fee(amount), "stamp": 0.0})
    return orders


# ---------------------------------------------------------------- 主流程
def run_pipeline(index_code: str = "000300", top_n: int = 50,
                 rebalance: int = 20, capital: float = 1_000_000.0,
                 max_single: float = 0.10, dry_run: bool = False,
                 push: bool = True,
                 db_path=None, label: str = "large",
                 max_positions: int | None = None,
                 strategy: str = "corrected",
                 _panel: pd.DataFrame | None = None) -> dict:
    store = ParquetStore()
    panel = (_panel if _panel is not None
             else _load_panel(store, index_code,
                              with_dividend=(strategy == "dividend")))
    latest = pd.Timestamp(panel["date"].max())
    if strategy == "dividend":
        W = turnover_lowvol_dividend_topn(panel, top_n=top_n,
                                          rebalance_days=rebalance)
    elif strategy == "topkdrop":
        # 与 corrected 相同因子，仅换手机制不同（TopkDropout）——干净 A/B（D-031）
        W = topk_dropout_topn(panel,
                              [("turnover_20", -1), ("vol_20", -1), ("rev_5", -1)],
                              topk=top_n, n_drop=5, rebalance_days=rebalance)
    else:
        W = turnover_lowvol_reversal_topn(panel, top_n=top_n,
                                          rebalance_days=rebalance)

    led = Ledger(db_path)
    # 1) 撮合到期订单（T+1 开盘价）
    fill_results: list[dict] = []
    if not dry_run:
        pend = led.pending_orders()
        lookup = {}
        last_dates = {}
        if not pend.empty:
            symbols = sorted({str(s) for s in pend["symbol"]})
            for d in sorted({pd.Timestamp(x) for x in pend["fill_date"]}):
                lookup[d] = _day_prices(store, d, symbols)
            for sym in symbols:
                try:
                    k = get_kline(sym, adjust="qfq", store=store)
                    last_dates[sym] = pd.to_datetime(k["date"]).max()
                except Exception:  # noqa: BLE001
                    last_dates[sym] = None
        fill_results = _fill_pending(led, lookup, latest, last_dates=last_dates)

    # 2) 账户状态 + 应用撮合结果
    snap_now = led.snapshot()
    last_snap_date = str(snap_now["date"].iloc[-1]) if not snap_now.empty else None
    cash, cur_shares, _ = _load_state(led)
    if cash == 0.0 and not cur_shares and snap_now.empty:
        cash = capital  # 账户初始化
    new_shares = dict(cur_shares)
    for r in fill_results:
        if r["status"] != "filled":
            continue
        if r["side"] == "buy":
            cash -= r["amount"] + r["fee"] + r["slip"]
            new_shares[r["symbol"]] = new_shares.get(r["symbol"], 0.0) + r["qty"]
        else:
            cash += r["amount"] - r["fee"] - r["stamp"] - r["slip"]
            new_shares[r["symbol"]] = max(0.0, new_shares.get(r["symbol"], 0.0) - r["qty"])

    # R3-04 自愈：上期快照之后已成交但未入快照的订单重放（崩溃窗口不再丢钱）
    # D-053：必须排除"本次刚撮合过"的成交，否则同一天成交会被算两遍
    # （topk 账户 10-27 因此 49 只持仓全部变成 2 倍、现金 -87 万）
    done_ids = {r["id"] for r in fill_results if r.get("status") == "filled"}
    if last_snap_date is not None:
        try:
            unre = _replay_fills(led, last_snap_date, latest, done_ids)
            for t in unre.itertuples(index=False):
                q = float(t.qty) if t.qty else (
                    float(t.amount) / float(t.price) if t.price else 0.0)
                if str(t.side) == "buy":
                    cash -= float(t.amount) + float(t.fee) + float(t.amount) * SLIP
                    new_shares[str(t.symbol)] = new_shares.get(str(t.symbol), 0.0) + q
                else:
                    cash += float(t.amount) - float(t.fee) - float(t.stamp_duty) \
                        - float(t.amount) * SLIP
                    new_shares[str(t.symbol)] = max(
                        0.0, new_shares.get(str(t.symbol), 0.0) - q)
                print(f"[replay] 自愈重放 {t.symbol} {t.side} {q:.0f} 股"
                      f"（fill_date={t.fill_date}）", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[replay] 自愈检查失败: {e}", flush=True)

    # D-053 防线：现金账户不该出现负现金；出现即说明记账路径重复（当年是自愈重放
    # 把同一天成交算了两遍，cash 被扣成 -869,765 而账面"看起来正常"）
    if cash < -1e-6:
        print(f"[warn] 现金为负 {cash:,.2f}——请核对是否有成交被重复记账"
              f"（trade_log 与 positions 的股数/成交净额是否一致）", flush=True)

    # 3) 最新收盘价与净值
    day = panel[panel["date"] == latest]
    prices_close = dict(zip(day["symbol"].astype(str), day["close"].astype(float), strict=False))
    mv = 0.0
    for s, sh in new_shares.items():
        px = prices_close.get(s)
        if px is None:  # 停牌/无最新价：取最近收盘
            try:
                df = get_kline(s, adjust="qfq", store=store)
                px = float(df[df["date"] <= latest]["close"].iloc[-1])
            except Exception:  # noqa: BLE001
                px = 0.0
        mv += sh * px
    nav_now = cash + mv

    # 4) 下单（调仓日或首次建仓；已有 pending 时不再重复下单）
    # D-057：全局调仓日只是"候选日"，账户自己的持有天数不够就不调仓。
    # 反例（事故前）：账户 10-20 半路建仓、11-02 是全局调仓日 → 只持有 9 个交易日
    # 就再整仓换一遍，等于凭空多出一次换手，回测里没有这一段。
    bootstrap = snap_now.empty and len(W) > 0
    cal = pd.DatetimeIndex(sorted(pd.to_datetime(panel["date"]).unique()))
    last_fill = led.last_fill_date()
    held_days = _held_trading_days(cal, last_fill, latest)
    cadence_ok = (held_days is None) or (held_days >= rebalance)
    scheduled = latest in W.index
    rebalance_day = scheduled and cadence_ok
    if scheduled and not cadence_ok and not bootstrap:
        print(f"[cadence] 距上次成交 {held_days} 个交易日 < {rebalance}，"
              f"本次全局调仓日跳过（账户按自己的持有天数走）", flush=True)
    orders_df = None
    if not dry_run and (rebalance_day or bootstrap) and led.pending_orders().empty:
        next_td = _next_trading_day(store, latest)
        if next_td is None:
            # R3-41：日历缺失/到期时不落"自然日"单（可能落周末被误拒单）
            print("[warn] 下一交易日无法确定（日历缺失/到期）——本期不挂单，请刷新日历后重跑")
        elif max_positions:
            # 小资金模式：整手组合，按分数挑最多 max_positions 只
            orders = _plan_small_orders(day, new_shares, prices_close, cash,
                                        max_positions=max_positions)
        else:
            w_row = (W.loc[latest] if rebalance_day else W.iloc[-1]).astype(float)
            nav_hist = None
            if not snap_now.empty:
                nav_hist = pd.Series(snap_now["total_nav"].values,
                                     index=pd.to_datetime(snap_now["date"]))
            w_series = apply_risk(w_row.to_frame().T, nav=nav_hist,
                                  max_single=max_single, max_total=1.0).iloc[0]
            target_w = {str(k): float(v) for k, v in w_series.items() if float(v) > 0}
            orders = _plan_orders(target_w, new_shares, prices_close, nav_now, cash)
        if orders:
            rows = [{
                "signal_date": str(latest.date()), "fill_date": str(next_td.date()),
                "symbol": o["symbol"], "side": o["side"], "qty": o["qty"],
                "price": o["price"], "amount": o["amount"], "fee": o["fee"],
                "stamp_duty": o["stamp"], "status": "pending",
                "note": "T+1 开盘成交（参考价=信号日收盘）",
            } for o in orders]
            orders_df = pd.DataFrame(rows)
            led.add_pending(orders_df)

    # 5) 持仓表（最新收盘价） + 快照 + 日报
    first_held = {}
    try:
        allpos = led.positions()
        first_held = allpos.groupby(allpos["symbol"].astype(str))["date"].min().to_dict()
    except Exception:  # noqa: BLE001
        pass
    pos_rows = []
    for s, sh in new_shares.items():
        if sh <= 0:
            continue
        px = prices_close.get(s)
        prev_px = None
        peak = None
        dd_peak = None
        try:
            df = get_kline(s, adjust="qfq", store=store)
            k2 = df[pd.to_datetime(df["date"]) <= latest]
            if px is None and len(k2):
                px = float(k2["close"].iloc[-1])
            if len(k2) >= 2:
                prev_px = float(k2["close"].iloc[-2])
            fd = first_held.get(str(s))
            if fd is not None and len(k2):
                k3 = k2[pd.to_datetime(k2["date"]) >= pd.Timestamp(fd)]["close"].astype(float)
                if len(k3) >= 2 and float(k3.max()) > 0:
                    peak = float(k3.max())
                    dd_peak = float(k3.iloc[-1]) / peak - 1.0
        except Exception:  # noqa: BLE001
            px = px or 0.0
        chg = (px / prev_px - 1.0) if (prev_px and px) else None
        pnl = (px - prev_px) * sh if (prev_px and px) else 0.0
        pos_rows.append({"symbol": s, "shares": sh, "close": px,
                         "prev_close": prev_px, "pct_chg": chg, "pnl": pnl,
                         "peak": peak, "dd_peak": dd_peak,
                         "weight": (sh * px) / nav_now if nav_now else 0.0})
    positions = pd.DataFrame(pos_rows)
    if len(positions):
        positions = positions.sort_values("weight", ascending=False)

    try:
        bench = get_kline(index_code, adjust="none", store=store)
        bench = bench.set_index(pd.to_datetime(bench["date"]))["close"].sort_index()
        bench_close = float(bench[bench.index <= latest].iloc[-1])
    except Exception:  # noqa: BLE001
        bench = None
        bench_close = float("nan")

    if not dry_run:
        led.record_day(str(latest.date()), cash=cash, market_value=mv,
                       positions=positions, trades=None,
                       benchmark_nav=bench_close)
        # R3-06 回填：(上期快照, latest] 中缺快照且有成交的日期，重放状态补写
        try:
            if last_snap_date is not None and bench is not None:
                gap_days = pd.date_range(pd.Timestamp(last_snap_date),
                                         pd.Timestamp(latest), freq="D")
                fills_all = led.unreconciled_fills(last_snap_date)
                filled_dates = (set(pd.to_datetime(fills_all["fill_date"]).dt.date)
                                if not fills_all.empty else set())
                for d in gap_days[:-1]:
                    ds = str(d.date())
                    if d.date() not in filled_dates or led.snapshot(date=ds).shape[0]:
                        continue
                    st = _replay_state(led, ds)
                    if st is None:
                        continue
                    pos_rows, mv_b = [], 0.0
                    for sym, sh in st["shares"].items():
                        if sh <= 0:
                            continue
                        try:
                            kk = get_kline(sym, adjust="qfq", store=store)
                            kk = kk[pd.to_datetime(kk["date"]) <= d]
                            px = float(kk["close"].iloc[-1]) if len(kk) else 0.0
                        except Exception:  # noqa: BLE001
                            px = 0.0
                        mv_b += sh * px
                        denom = st["cash"] + mv_b
                        pos_rows.append({"symbol": sym, "shares": sh, "close": px,
                                         "weight": sh * px / denom if denom else 0.0})
                    bb = bench[bench.index <= d]
                    led.record_day(ds, cash=st["cash"], market_value=mv_b,
                                   positions=pd.DataFrame(pos_rows) if pos_rows else None,
                                   benchmark_nav=float(bb.iloc[-1]) if len(bb) else None)
                    print(f"[backfill] 已补写 {ds} 快照（延迟撮合，R3-06）", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[backfill] 回填失败: {e}", flush=True)
        # 每日操作记录：同一天重复运行只更新这一条（不新增行、不占空间）
        try:
            filled_n = sum(1 for r in fill_results if r["status"] == "filled")
            pend_n = len(led.pending_orders())
            led.record_daily_op(
                str(latest.date()), label, "pipeline", "done",
                f"fills={filled_n} pending={pend_n} nav={nav_now:,.2f}")
        except Exception as e:  # noqa: BLE001
            print(f"[daily_ops] 记录失败: {e}")
        # D-062：累计费用与账本自检必须在 close 之前算好——close 后再 connect
        # 会重开连接且永不关闭（Windows 文件锁），与 D-029 修过的是同一类错误
        cum_cost = _cum_cost(led)
        check_lines, check_ok = _self_check(
            led, cash, positions if len(positions) else pd.DataFrame())
        # 先取待成交列表再关连接：close 后再调用会重开连接且永不关闭（Windows 文件锁）
        pending_after = led.pending_orders()
        led.daily_backup()
        led.close()
    else:
        pending_after = pd.DataFrame()
        cum_cost = _cum_cost(led)
        check_lines, check_ok = _self_check(
            led, cash, positions if len(positions) else pd.DataFrame())

    # ---- 日报 ----
    report_dir = STORE_DIR / "reports" / label
    report_dir.mkdir(parents=True, exist_ok=True)
    # 昨日净值：必须是"日期早于今天"的最后一条快照（同一天重跑时不能拿自己当昨日）
    prev_nav = nav_now
    if not snap_now.empty:
        prior = snap_now[pd.to_datetime(snap_now["date"]) < latest]
        if len(prior):
            prev_nav = float(prior["total_nav"].iloc[-1])
    day_pnl = nav_now - prev_nav
    day_ret = (nav_now / prev_nav - 1.0) if prev_nav else 0.0
    base_nav = float(snap_now["total_nav"].iloc[0]) if not snap_now.empty else nav_now
    cum_ret = (nav_now / base_nav - 1.0) if base_nav else 0.0
    if len(positions) and "pct_chg" in positions.columns:
        up = int((positions["pct_chg"] > 0).sum())
        down = int((positions["pct_chg"] < 0).sum())
        flat = int((positions["pct_chg"].isna() | (positions["pct_chg"] == 0)).sum())
        chg_txt = f"涨 {up} / 跌 {down} / 平 {flat}"
    else:
        chg_txt = "-"
    lines = [f"# 影子盘日报[{label}] {latest.date()}", "", "## 账户",
             f"- 现金: {cash:,.2f}",
             f"- 持仓市值: {mv:,.2f}",
             f"- 总净值: {nav_now:,.2f}",
             f"- 今日盈亏: {day_pnl:+,.2f}（{day_ret:+.2%}）",
             f"- 累计收益: {cum_ret:+.2%}",
             f"- 累计交易费用（含滑点估算）: {cum_cost:,.2f} 元",
             f"- 涨跌家数: {chg_txt}",
             f"- 基准(沪深300): {bench_close:,.2f}",
             f"- 持仓数: {len(positions)}",
             "", "## 今日撮合（T+1 开盘价）"]
    if fill_results:
        lines.append(f"- 处理 {len(fill_results)} 笔")
        lines.append("")
        lines.append("| 代码 | 方向 | 数量 | 价格 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for r in fill_results:
            if r["status"] == "filled":
                lines.append(f"| {r['symbol']} | {r['side']} | {r['qty']:.0f} | "
                             f"{r['price']:.2f} | 成交 |")
            else:
                lines.append(f"| {r['symbol']} | - | - | - | {r['status']} |")
    else:
        lines.append("- 无（没有到期的待成交订单）")
    filled = [r for r in fill_results if r["status"] == "filled"]
    if filled:
        c_fee = sum(r["fee"] for r in filled)
        c_slip = sum(r["slip"] for r in filled)
        c_stamp = sum(r["stamp"] for r in filled)
        c_trans = sum(r["amount"] * TRANSFER for r in filled)
        lines.append("")
        lines.append("## 今日费用")
        lines.append(f"- 佣金(含最低 {MIN_FEE:.0f} 元/笔): {c_fee:,.2f}")
        lines.append(f"- 印花税: {c_stamp:,.2f}")
        lines.append(f"- 过户费: {c_trans:,.2f}")
        lines.append(f"- 滑点(估算): {c_slip:,.2f}")
        lines.append(f"- 合计: {c_fee + c_slip + c_stamp + c_trans:,.2f}")

    lines += ["", "## 待成交订单"]
    if not dry_run and len(pending_after):
        lines.append(f"- {len(pending_after)} 笔（成交日 {pd.Timestamp(pending_after['fill_date'].iloc[0]).date()} 开盘）")
        lines.append("")
        lines.append("| 代码 | 方向 | 数量 | 参考价 |")
        lines.append("|---|---|---|---|")
        for r in pending_after.itertuples(index=False):
            lines.append(f"| {r.symbol} | {r.side} | {r.qty:.0f} | {r.price:.2f} |")
    else:
        lines.append("- 无")

    # D-057：今天是全局调仓日、但账户持有天数不够 → 明确写进日报，避免看起来"该调没调"
    if scheduled and not cadence_ok and not bootstrap:
        lines += ["", f"> 调仓节奏：距上次成交 {held_days} 个交易日 < {rebalance} 天，"
                      f"按账户自己的持有周期，本次不调仓（D-057）"]

    # D-053 防线：账本自检四行（负现金/股数翻倍/权重超100%/滞留 pending），
    # 已在 close 前算好（D-062 连接泄漏修复）
    lines += check_lines

    # 持仓预警：距持有期高点回撤 >=10%（仅提醒；个股止损类规则回测为负贡献，见 D-026）
    alert_rows = sorted(
        (r for r in pos_rows
         if r.get("dd_peak") is not None and r["dd_peak"] <= -0.10),
        key=lambda r: r["dd_peak"])
    if alert_rows:
        lines += ["", "## 持仓预警（距持有期高点回撤 ≥10%，仅提醒不自动处置）",
                  "| 代码 | 现价 | 持有期高点 | 回撤 |", "|---|---|---|---|"]
        for r in alert_rows:
            lines.append(f"| {r['symbol']} | {r['close']:.2f} | {r['peak']:.2f} |"
                         f" {r['dd_peak']:+.1%} |")
        lines.append("> 去留由 20 日调仓的分数重排决定；人工否决请用控制台「调仓审批」。")

    if len(positions):
        lines += ["", "## 前 10 大持仓",
                  "| 代码 | 股数 | 收盘 | 涨跌幅 | 当日盈亏 | 权重 |",
                  "|---|---|---|---|---|---|"]
        for r in positions.head(10).itertuples(index=False):
            chg = f"{r.pct_chg:+.2%}" if pd.notna(r.pct_chg) else "-"
            lines.append(f"| {r.symbol} | {r.shares:.0f} | {r.close:.2f} | {chg} | "
                         f"{r.pnl:+,.0f} | {r.weight:.2%} |")

    lines += ["", "> 成交口径：T 日收盘挂单，T+1 开盘价撮合；"
                  "涨停买不进/跌停卖不出/停牌记为拒单。数据更新由 02/04 负责。"]
    report = "\n".join(lines)
    report_path = report_dir / f"{latest.date()}.md"
    # 该交易日是否已生成过日报（重复运行/周末重跑）——决定是否重复推送邮件
    already_reported = report_path.exists()
    # D-062：发送失败标记——"报告文件存在"作为已推送水位会吞掉 SMTP 失败
    # （写文件成功+发送失败 → 当日永不补发）。失败落标记，重跑时重试。
    send_fail_marker = report_path.with_name(report_path.name + ".send_failed")
    if not dry_run:
        report_path.write_text(report, encoding="utf-8")

    print(report)
    if not dry_run:
        print(f"\n报表已写入: {report_path}")
        # 邮件推送：每个交易日的日报只推一次（D-029）——
        # 重跑/歇市重跑静默更新报告但不重发；上次发送失败（有标记）则本次重试
        if push and already_reported and not send_fail_marker.exists():
            print(f"[notifier] {latest.date()} 日报此前已推送，跳过重复发送")
        elif push:
            try:
                from src.live.notifier import is_configured, send_report
                if is_configured():
                    # D-059：自检失败时邮件标题必须带警示——收件人不开电脑也知道要处理
                    subj = f"影子盘日报[{label}] {latest.date()}"
                    if not check_ok:
                        subj = f"⚠️自检异常 {subj}"
                    sent = send_report(subj, report)
                    if sent:
                        send_fail_marker.unlink(missing_ok=True)
                    else:
                        send_fail_marker.write_text(
                            f"send failed {pd.Timestamp.now()}\n", encoding="utf-8")
                        print("[notifier] 推送失败——已落标记，下次运行自动重试（D-062）")
            except Exception as e:  # noqa: BLE001
                send_fail_marker.write_text(
                    f"send error {pd.Timestamp.now()}: {e}\n", encoding="utf-8")
                print(f"[notifier] 推送异常（已落标记，下次运行自动重试）: {e}")
    if not check_ok:
        print(">> [self-check] ⚠️ 自检未通过（负现金/股数不符/权重超限）——请核对账本，"
              "不要只看净值（D-053 的教训：两处错误会互相抵消）", flush=True)
    print(f"（dry_run={dry_run}，撮合 {sum(1 for r in fill_results if r['status']=='filled')} 笔，"
          f"拒单 {sum(1 for r in fill_results if r['status'].startswith('rejected'))} 笔，"
          f"待成交 {len(pending_after) if not dry_run else 0} 笔，净值 {nav_now:,.2f}）")
    return {"date": str(latest.date()), "nav": nav_now, "fills": fill_results,
            "pending": len(pending_after) if not dry_run else 0,
            "report": str(report_path), "report_text": report,
            "new_report": not already_reported,
            "emailed": bool(push and not already_reported),
            "self_check_ok": check_ok,
            "day_ret": day_ret, "cum_ret": cum_ret}


def _cum_cost(led) -> float:
    """累计交易代价：佣金+印花+滑点估算（已成交口径）。"""
    try:
        f = pd.read_sql_query(
            "SELECT COALESCE(SUM(amount),0) a, COALESCE(SUM(fee),0) f, "
            "COALESCE(SUM(stamp_duty),0) s FROM trade_log WHERE status='filled'",
            led.connect())
        return float(f["f"].iloc[0]) + float(f["s"].iloc[0]) + float(f["a"].iloc[0]) * SLIP
    except Exception:  # noqa: BLE001
        return 0.0


def combine_reports(day: str, parts: list[dict]) -> str:
    """把多账户日报合并为一封邮件正文（D-030）。

    parts: [{"title": 账户标题, "text": 单账户日报全文,
             "nav": 净值, "day_ret": 当日收益率, "cum_ret": 累计收益率}, ...]
    """
    lines = [f"# QuantDex 影子盘日报 {day}", "", "## 账户速览", ""]
    for p in parts:
        if p.get("nav") is None:
            lines.append(f"- **{p['title']}**（暂无数据）")
        else:
            lines.append(f"- **{p['title']}** 净值 {p['nav']:,.2f}"
                         f"（今日 {p.get('day_ret') or 0.0:+.2%}"
                         f" / 累计 {p.get('cum_ret') or 0.0:+.2%}）")
    lines.append("")
    for p in parts:
        lines.append(f"{'=' * 16} {p['title']} {'=' * 16}")
        # 去掉单账户报告的首行标题（避免与合并标题重复）
        lines.append(p["text"].split("\n", 1)[-1].lstrip("\n"))
        lines.append("")
    return "\n".join(lines)
