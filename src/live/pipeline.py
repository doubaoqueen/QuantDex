# -*- coding: utf-8 -*-
"""
影子盘每日流水线（MVP）：
收盘后运行 -> 读取本地数据 -> 计算目标权重(经风控) -> 模拟成交(收盘价) ->
更新账本(快照/持仓/成交) -> 生成纯文本日报。

约定（MVP 简化，已写入日报与账本 note）：
- 模拟成交价 = 当日收盘价（真实执行应为 T+1 开盘；账本 fill_date 字段已预留）；
- 只在策略调仓日（每 rebalance 个交易日）产生交易，其余交易日仅做净值快照；
- 幂等：同一天重复运行不会重复记账（成交按 signal_date 去重，快照/持仓覆盖写）；
- 数据更新不在此模块内（由 02/04 负责），便于离线测试与关注点分离。

用法：
    python scripts/08_daily_pipeline.py              # 正常运行(记录/调仓)
    python scripts/08_daily_pipeline.py --dry-run    # 只计算不落账本
"""
from __future__ import annotations

import datetime as _dt
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


def _load_panel(store: ParquetStore, index_code: str) -> pd.DataFrame:
    pit = universe.load_pit_constituents(index_code, store)
    symbols = universe.pit_union_symbols(index_code, store)
    panel = build_factor_panel(store, symbols, start="20180101",
                               pit_constituents=pit)
    return panel


def _load_state(led: Ledger) -> tuple[float, dict[str, float], str | None]:
    snap = led.snapshot()
    if snap.empty:
        return 0.0, {}, None
    last_date = str(snap["date"].iloc[-1])
    cash = float(snap["cash"].iloc[-1])
    pos = led.positions(last_date)
    shares = {str(r.symbol): float(r.shares) for r in pos.itertuples(index=False)}
    return cash, shares, last_date


def _plan_orders(target_w: dict[str, float], cur_shares: dict[str, float],
                 prices: dict[str, float], nav: float, cash: float) -> list[dict]:
    """生成成交计划（先卖后买，买入超出可用现金时按比例缩放）。"""
    orders: list[dict] = []
    # 卖出（不在目标里的 / 权重变小的）
    for sym, cur in cur_shares.items():
        close = prices.get(sym)
        if close is None or close <= 0:
            continue
        tgt = target_w.get(sym, 0.0) * nav
        tgt_sh = np.floor(tgt / (close * LOT)) * LOT
        delta = tgt_sh - cur
        if delta < 0:
            qty = -delta
            amount = qty * close
            fee = amount * FEE
            stamp = amount * STAMP
            slip = amount * SLIP
            cash += amount - fee - stamp - slip
            orders.append({"side": "sell", "symbol": sym, "qty": qty,
                           "price": close, "amount": amount,
                           "fee": fee, "stamp_duty": stamp, "slip": slip})
    # 买入
    buys: list[dict] = []
    for sym, w in target_w.items():
        close = prices.get(sym)
        if close is None or close <= 0:
            continue
        tgt_sh = np.floor(w * nav / (close * LOT)) * LOT
        cur = cur_shares.get(sym, 0.0)
        delta = tgt_sh - cur
        if delta > 0:
            amount = delta * close
            fee = amount * FEE
            slip = amount * SLIP
            buys.append({"side": "buy", "symbol": sym, "qty": delta,
                         "price": close, "amount": amount,
                         "fee": fee, "stamp_duty": 0.0, "slip": slip})
    need = sum(b["amount"] + b["fee"] + b["slip"] for b in buys)
    if need > cash and need > 0:  # 现金不足：按比例缩放
        scale = cash / need
        for b in buys:
            b["qty"] = np.floor(b["qty"] * scale / LOT) * LOT
            if b["qty"] <= 0:
                b["qty"] = 0.0
        buys = [b for b in buys if b["qty"] > 0]
        for b in buys:
            b["amount"] = b["qty"] * b["price"]
            b["fee"] = b["amount"] * FEE
            b["slip"] = b["amount"] * SLIP
    for b in buys:
        cash -= b["amount"] + b["fee"] + b["slip"]
        orders.append(b)
    return orders


def run_pipeline(index_code: str = "000300", top_n: int = 50,
                 rebalance: int = 20, capital: float = 1_000_000.0,
                 max_single: float = 0.10, dry_run: bool = False,
                 _panel: pd.DataFrame | None = None) -> dict:
    store = ParquetStore()
    panel = _panel if _panel is not None else _load_panel(store, index_code)
    latest = pd.Timestamp(panel["date"].max())
    W = turnover_lowvol_reversal_topn(panel, top_n=top_n, rebalance_days=rebalance)

    led = Ledger()
    cash, cur_shares, last_date = _load_state(led)
    if cash == 0.0 and not cur_shares:
        cash = capital  # 初始化

    # 当日价格（latest 收盘）
    day = panel[panel["date"] == latest]
    prices = dict(zip(day["symbol"].astype(str), day["close"].astype(float)))

    # 风控：单票/总仓 + 回撤断路器（基于账本净值历史）
    snap_hist = led.snapshot()
    nav_hist = None
    if not snap_hist.empty:
        nav_hist = pd.Series(snap_hist["total_nav"].values,
                             index=pd.to_datetime(snap_hist["date"]))
    rebalance_day = latest in W.index
    bootstrap = (len(snap_hist) == 0) and len(W) > 0  # 首次运行：空账户按最近信号建仓
    target_w: dict[str, float] = {}
    if rebalance_day or bootstrap:
        w_row = (W.loc[latest] if rebalance_day else W.iloc[-1]).astype(float)
        w_series = apply_risk(w_row.to_frame().T, nav=nav_hist,
                              max_single=max_single, max_total=1.0).iloc[0]
        target_w = {str(k): float(v) for k, v in w_series.items() if float(v) > 0}

    # 净值（按最新收盘 marking）
    cur_mv = sum(cur_shares.get(s, 0.0) * prices.get(s, 0.0) for s in cur_shares)
    nav_now = cash + cur_mv

    orders: list[dict] = []
    if rebalance_day or bootstrap:
        orders = _plan_orders(target_w, cur_shares, prices, nav_now, cash)

    # 执行模拟成交（收盘价）
    new_shares = dict(cur_shares)
    for o in orders:
        sym = o["symbol"]
        if o["side"] == "buy":
            new_shares[sym] = new_shares.get(sym, 0.0) + o["qty"]
        else:
            new_shares[sym] = max(0.0, new_shares.get(sym, 0.0) - o["qty"])
        o["amount"] = o["qty"] * o["price"]
        if o["side"] == "sell":
            o["stamp_duty"] = o["amount"] * STAMP
            o["fee"] = o["amount"] * FEE
        else:
            o["stamp_duty"] = 0.0
            o["fee"] = o["amount"] * FEE

    new_cash = cash
    for o in orders:
        gross = o["amount"]
        cost = o["fee"] + o["stamp_duty"] + (gross * SLIP)
        new_cash += (-gross - cost) if o["side"] == "buy" else (gross - cost)

    new_mv = sum(new_shares.get(s, 0.0) * prices.get(s, 0.0) for s in new_shares)
    total_nav = new_cash + new_mv

    # 基准
    try:
        bench = get_kline(index_code, adjust="none", store=store)
        bench_close = float(bench[bench["date"] <= latest]["close"].iloc[-1])
    except Exception:  # noqa: BLE001
        bench_close = float("nan")

    # 持仓表 + 成交表
    pos_rows = []
    for sym, sh in new_shares.items():
        if sh <= 0:
            continue
        close = prices.get(sym, 0.0)
        pos_rows.append({"symbol": sym, "shares": sh, "close": close,
                         "weight": (sh * close) / total_nav if total_nav else 0.0})
    positions = pd.DataFrame(pos_rows).sort_values("weight", ascending=False)

    trade_rows = []
    for o in orders:
        trade_rows.append({
            "signal_date": str(latest.date()), "fill_date": str(latest.date()),
            "symbol": o["symbol"], "side": o["side"], "qty": o["qty"],
            "price": o["price"], "amount": o["amount"],
            "fee": o["fee"], "stamp_duty": o["stamp_duty"],
            "status": "filled", "note": "模拟成交@收盘价",
        })
    trades = pd.DataFrame(trade_rows)

    # 幂等：同日重复运行不重复写成交
    if not dry_run and not trades.empty:
        existing = led.trades()
        if len(existing) and str(latest.date()) in set(existing["signal_date"].astype(str)):
            trades = trades.iloc[0:0]
            print("（同一天已有成交记录，跳过成交写入）")

    if not dry_run:
        led.record_day(str(latest.date()), cash=new_cash, market_value=new_mv,
                       positions=positions, trades=trades,
                       benchmark_nav=bench_close)
        led.daily_backup()
        led.close()

    # ---- 日报 ----
    report_dir = STORE_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 影子盘日报 {latest.date()}",
        "",
        "## 账户",
        f"- 现金: {new_cash:,.2f}",
        f"- 持仓市值: {new_mv:,.2f}",
        f"- 总净值: {total_nav:,.2f}",
        f"- 基准(沪深300): {bench_close:,.2f}",
        f"- 持仓数: {len(positions)}",
        "",
        "## 今日操作",
        f"- 策略调仓日: {'是' if rebalance_day else ('否（首次建仓 bootstrap）' if bootstrap else '否（仅净值快照）')}",
        f"- 模拟成交: {len(trades)} 笔（收盘价口径）",
    ]
    if len(trades):
        lines.append("")
        lines.append("| 代码 | 方向 | 数量 | 价格 | 金额 |")
        lines.append("|---|---|---|---|---|")
        for r in trades.itertuples(index=False):
            lines.append(f"| {r.symbol} | {r.side} | {r.qty:.0f} | {r.price:.2f} | {r.amount:,.0f} |")
    if len(positions):
        lines.append("")
        lines.append("## 前 10 大持仓")
        lines.append("| 代码 | 股数 | 收盘 | 权重 |")
        lines.append("|---|---|---|---|")
        for r in positions.head(10).itertuples(index=False):
            lines.append(f"| {r.symbol} | {r.shares:.0f} | {r.close:.2f} | {r.weight:.2%} |")
    lines += ["", "> 模拟成交价=当日收盘价（MVP 简化）；数据更新由 02/04 负责。"]
    report = "\n".join(lines)
    report_path = report_dir / f"{latest.date()}.md"
    if not dry_run:
        report_path.write_text(report, encoding="utf-8")

    print(report)
    if not dry_run:
        print(f"\n报表已写入: {report_path}")
    print(f"（dry_run={dry_run}，调仓日={rebalance_day}，成交 {len(trades)} 笔，净值 {total_nav:,.2f}）")
    return {"date": str(latest.date()), "nav": total_nav, "rebalance": rebalance_day,
            "trades": len(trades), "report": str(report_path)}
