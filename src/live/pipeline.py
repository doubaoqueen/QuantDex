# -*- coding: utf-8 -*-
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

from pathlib import Path

import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.factors import build_factor_panel
from src.data import universe
from src.data.market import get_kline
from src.data.storage import ParquetStore
from src.live.ledger import Ledger
from src.risk.risk import apply_risk
from src.strategies.multi_factor import turnover_lowvol_reversal_topn

FEE = 0.00025
SLIP = 0.0005
STAMP = 0.0005
LOT = 100
# 涨跌停幅度：主板/中小板 10%；创业板(300/301)/科创板(688) 20%
_GEM_PREFIX = ("300", "301", "688")


def _board_limit(symbol: str) -> float:
    return 0.20 if str(symbol).startswith(_GEM_PREFIX) else 0.10


# ---------------------------------------------------------------- 数据准备
def _load_panel(store: ParquetStore, index_code: str) -> pd.DataFrame:
    pit = universe.load_pit_constituents(index_code, store)
    symbols = universe.pit_union_symbols(index_code, store)
    return build_factor_panel(store, symbols, start="20180101",
                              pit_constituents=pit)


def _load_state(led: Ledger) -> tuple[float, dict[str, float], str | None]:
    snap = led.snapshot()
    if snap.empty:
        return 0.0, {}, None
    last_date = str(snap["date"].iloc[-1])
    cash = float(snap["cash"].iloc[-1])
    pos = led.positions(last_date)
    shares = {str(r.symbol): float(r.shares) for r in pos.itertuples(index=False)}
    return cash, shares, last_date


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


# ---------------------------------------------------------------- 撮合
def _fill_pending(led: Ledger, price_lookup: dict,
                  latest: pd.Timestamp) -> list[dict]:
    """撮合 fill_date ≤ latest 的待成交订单；返回每单处理结果。"""
    pend = led.pending_orders()
    if pend.empty:
        return []
    results = []
    for o in pend.itertuples(index=False):
        fd = pd.Timestamp(o.fill_date)
        if fd > latest:
            results.append({"id": o.id, "symbol": o.symbol, "status": "pending",
                            "msg": f"未到成交日({fd.date()})"})
            continue
        dp = price_lookup.get(fd, {}).get(str(o.symbol))
        if dp is None:
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
        fee = amount * FEE
        stamp = amount * STAMP if o.side == "sell" else 0.0
        slip = amount * SLIP
        led.mark_filled(o.id, str(fd.date()), op, amount, fee, stamp)
        results.append({"id": o.id, "symbol": o.symbol, "side": o.side,
                        "status": "filled", "qty": qty, "price": op,
                        "amount": amount, "fee": fee, "stamp": stamp,
                        "slip": slip, "msg": "开盘价成交"})
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
                           "fee": amount * FEE, "stamp": amount * STAMP})
            cash += amount - amount * FEE - amount * STAMP - amount * SLIP
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
                         "fee": amount * FEE, "stamp": 0.0})
    need = sum(b["amount"] + b["fee"] + b["amount"] * SLIP for b in buys)
    if need > cash and need > 0:
        scale = cash / need
        for b in buys:
            b["qty"] = float(np.floor(b["qty"] * scale / LOT) * LOT)
        buys = [b for b in buys if b["qty"] > 0]
        for b in buys:
            b["amount"] = b["qty"] * b["price"]
            b["fee"] = b["amount"] * FEE
    for b in buys:
        cash -= b["amount"] + b["fee"] + b["amount"] * SLIP
        orders.append(b)
    return orders


# ---------------------------------------------------------------- 主流程
def run_pipeline(index_code: str = "000300", top_n: int = 50,
                 rebalance: int = 20, capital: float = 1_000_000.0,
                 max_single: float = 0.10, dry_run: bool = False,
                 _panel: pd.DataFrame | None = None) -> dict:
    store = ParquetStore()
    panel = _panel if _panel is not None else _load_panel(store, index_code)
    latest = pd.Timestamp(panel["date"].max())
    W = turnover_lowvol_reversal_topn(panel, top_n=top_n,
                                      rebalance_days=rebalance)

    led = Ledger()
    # 1) 撮合到期订单（T+1 开盘价）
    fill_results: list[dict] = []
    if not dry_run:
        pend = led.pending_orders()
        lookup = {}
        if not pend.empty:
            symbols = sorted({str(s) for s in pend["symbol"]})
            for d in sorted({pd.Timestamp(x) for x in pend["fill_date"]}):
                lookup[d] = _day_prices(store, d, symbols)
        fill_results = _fill_pending(led, lookup, latest)

    # 2) 账户状态 + 应用撮合结果
    snap_now = led.snapshot()
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

    # 3) 最新收盘价与净值
    day = panel[panel["date"] == latest]
    prices_close = dict(zip(day["symbol"].astype(str), day["close"].astype(float)))
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
    rebalance_day = latest in W.index
    bootstrap = snap_now.empty and len(W) > 0
    orders_df = None
    if not dry_run and (rebalance_day or bootstrap) and led.pending_orders().empty:
        w_row = (W.loc[latest] if rebalance_day else W.iloc[-1]).astype(float)
        nav_hist = None
        if not snap_now.empty:
            nav_hist = pd.Series(snap_now["total_nav"].values,
                                 index=pd.to_datetime(snap_now["date"]))
        w_series = apply_risk(w_row.to_frame().T, nav=nav_hist,
                              max_single=max_single, max_total=1.0).iloc[0]
        target_w = {str(k): float(v) for k, v in w_series.items() if float(v) > 0}
        next_td = _next_trading_day(store, latest) or (latest + pd.Timedelta(days=1))
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
    pos_rows = []
    for s, sh in new_shares.items():
        if sh <= 0:
            continue
        px = prices_close.get(s)
        if px is None:
            try:
                df = get_kline(s, adjust="qfq", store=store)
                px = float(df[df["date"] <= latest]["close"].iloc[-1])
            except Exception:  # noqa: BLE001
                px = 0.0
        pos_rows.append({"symbol": s, "shares": sh, "close": px,
                         "weight": (sh * px) / nav_now if nav_now else 0.0})
    positions = pd.DataFrame(pos_rows)
    if len(positions):
        positions = positions.sort_values("weight", ascending=False)

    try:
        bench = get_kline(index_code, adjust="none", store=store)
        bench_close = float(bench[bench["date"] <= latest]["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        bench_close = float("nan")

    if not dry_run:
        led.record_day(str(latest.date()), cash=cash, market_value=mv,
                       positions=positions, trades=None,
                       benchmark_nav=bench_close)
        led.daily_backup()
        led.close()

    pending_after = led.pending_orders() if not dry_run else pd.DataFrame()

    # ---- 日报 ----
    report_dir = STORE_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# 影子盘日报 {latest.date()}", "", "## 账户",
             f"- 现金: {cash:,.2f}",
             f"- 持仓市值: {mv:,.2f}",
             f"- 总净值: {nav_now:,.2f}",
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

    if len(positions):
        lines += ["", "## 前 10 大持仓", "| 代码 | 股数 | 收盘 | 权重 |",
                  "|---|---|---|---|"]
        for r in positions.head(10).itertuples(index=False):
            lines.append(f"| {r.symbol} | {r.shares:.0f} | {r.close:.2f} | {r.weight:.2%} |")

    lines += ["", "> 成交口径：T 日收盘挂单，T+1 开盘价撮合；"
                  "涨停买不进/跌停卖不出/停牌记为拒单。数据更新由 02/04 负责。"]
    report = "\n".join(lines)
    report_path = report_dir / f"{latest.date()}.md"
    if not dry_run:
        report_path.write_text(report, encoding="utf-8")

    print(report)
    if not dry_run:
        print(f"\n报表已写入: {report_path}")
    print(f"（dry_run={dry_run}，撮合 {sum(1 for r in fill_results if r['status']=='filled')} 笔，"
          f"拒单 {sum(1 for r in fill_results if r['status'].startswith('rejected'))} 笔，"
          f"待成交 {len(pending_after) if not dry_run else 0} 笔，净值 {nav_now:,.2f}）")
    return {"date": str(latest.date()), "nav": nav_now, "fills": fill_results,
            "pending": len(pending_after) if not dry_run else 0,
            "report": str(report_path)}
