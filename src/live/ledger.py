# -*- coding: utf-8 -*-
"""
模拟盘账本（SQLite，WAL 模式）。

三张表：
- account_snapshot(date PK, cash, market_value, total_nav, benchmark_nav, created_at)
- positions(date, symbol, shares, close, weight, PK(date,symbol))
- trade_log(id PK, signal_date, fill_date, symbol, side, qty, price, amount,
            fee, stamp_duty, status, note)
  status: filled / rejected_limit_up / rejected_suspended / partial

用法：
    from src.live.ledger import Ledger
    led = Ledger()                      # 默认 data/store/sim/ledger.sqlite
    led.record_day("2026-10-19", cash=500000, market_value=480000,
                   positions=df, trades=df2, benchmark_nav=1.23)
    led.snapshot().tail()
    led.daily_backup()                  # 每日备份（保留最近 30 份）
"""
from __future__ import annotations

import datetime as _dt
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

from config import STORE_DIR

_DEFAULT_DB = STORE_DIR / "sim" / "ledger.sqlite"
_BACKUP_DIR = STORE_DIR / "sim" / "backups"
_KEEP_BACKUPS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_snapshot (
    date TEXT PRIMARY KEY,
    cash REAL NOT NULL,
    market_value REAL NOT NULL,
    total_nav REAL NOT NULL,
    benchmark_nav REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    shares REAL NOT NULL,
    close REAL NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date TEXT,
    fill_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    fee REAL DEFAULT 0,
    stamp_duty REAL DEFAULT 0,
    status TEXT DEFAULT 'filled',
    note TEXT
);
CREATE TABLE IF NOT EXISTS daily_ops (
    date TEXT NOT NULL,
    account TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT,
    summary TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (date, account, action)
);
"""

_TRADE_COLS = ["signal_date", "fill_date", "symbol", "side", "qty", "price",
               "amount", "fee", "stamp_duty", "status", "note"]


class Ledger:
    """模拟盘账本。开一次即建表，WAL 模式便于并发读。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or _DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
        return self._conn

    def close(self) -> None:
        """关闭连接（Windows 上必须关闭后才能删除/移动数据库文件）。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -------------------------------------------------- 写入
    def record_day(self, date: str, cash: float, market_value: float,
                   positions: pd.DataFrame | None = None,
                   trades: pd.DataFrame | None = None,
                   benchmark_nav: float | None = None) -> "Ledger":
        """写入某日模拟记录（快照 + 持仓 + 成交，同一事务）。"""
        conn = self.connect()
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO account_snapshot"
                "(date,cash,market_value,total_nav,benchmark_nav,created_at)"
                " VALUES (?,?,?,?,?,?)",
                (date, float(cash), float(market_value),
                 float(cash) + float(market_value), benchmark_nav, now))
            if positions is not None and len(positions):
                rows = [(date, str(r.symbol), float(r.shares),
                         float(r.close), float(r.weight))
                        for r in positions.itertuples(index=False)]
                conn.executemany(
                    "INSERT OR REPLACE INTO positions(date,symbol,shares,close,weight)"
                    " VALUES (?,?,?,?,?)", rows)
            if trades is not None and len(trades):
                t = trades.copy()
                for c in _TRADE_COLS:
                    if c not in t.columns:
                        t[c] = None
                t = t[_TRADE_COLS]
                conn.executemany(
                    "INSERT INTO trade_log(signal_date,fill_date,symbol,side,qty,"
                    "price,amount,fee,stamp_duty,status,note)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [tuple(None if (v is None or (isinstance(v, float) and pd.isna(v)))
                           else v for v in row) for row in t.itertuples(index=False)])
        return self

    # -------------------------------------------------- 读取
    def snapshot(self, date: str | None = None) -> pd.DataFrame:
        q = "SELECT * FROM account_snapshot"
        args: tuple = ()
        if date:
            q += " WHERE date = ?"
            args = (date,)
        return pd.read_sql_query(q + " ORDER BY date", self.connect(), params=args)

    def positions(self, date: str | None = None) -> pd.DataFrame:
        q = "SELECT * FROM positions"
        args: tuple = ()
        if date:
            q += " WHERE date = ?"
            args = (date,)
        return pd.read_sql_query(q + " ORDER BY date, symbol", self.connect(), params=args)

    def trades(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM trade_log ORDER BY id", self.connect())

    # -------------------------------------------------- 待成交订单（T+1 两段式）
    def add_pending(self, orders: pd.DataFrame) -> int:
        """写入待成交订单（status='pending'），返回写入条数。

        订单字段：signal_date / fill_date(预期成交日) / symbol / side / qty /
        price(参考价,信号日收盘) / amount / fee / stamp_duty / status / note
        """
        if orders is None or len(orders) == 0:
            return 0
        t = orders.copy()
        for c in _TRADE_COLS:
            if c not in t.columns:
                t[c] = None
        t = t[_TRADE_COLS]
        rows = [tuple(None if (v is None or (isinstance(v, float) and pd.isna(v))) else v
                      for v in r) for r in t.itertuples(index=False)]
        conn = self.connect()
        with conn:
            conn.executemany(
                "INSERT INTO trade_log(signal_date,fill_date,symbol,side,qty,price,"
                "amount,fee,stamp_duty,status,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                rows)
        return len(rows)

    def pending_orders(self) -> pd.DataFrame:
        """返回全部待成交订单。"""
        return pd.read_sql_query(
            "SELECT * FROM trade_log WHERE status='pending' ORDER BY signal_date, id",
            self.connect())

    def mark_filled(self, trade_id: int, fill_date: str, price: float,
                    amount: float, fee: float, stamp_duty: float) -> None:
        """把待成交订单标记为已完成（写入真实成交价与费用）。"""
        conn = self.connect()
        with conn:
            conn.execute(
                "UPDATE trade_log SET fill_date=?, price=?, amount=?, fee=?, "
                "stamp_duty=?, status='filled' WHERE id=?",
                (fill_date, price, amount, fee, stamp_duty, trade_id))

    def mark_rejected(self, trade_id: int, status: str, note: str = "") -> None:
        """把待成交订单标记为拒单（涨停/跌停/停牌）。"""
        conn = self.connect()
        with conn:
            conn.execute("UPDATE trade_log SET status=?, note=? WHERE id=?",
                         (status, note, trade_id))

    # -------------------------------------------------- 备份
    # -------------------------------------------------- 每日操作记录（一天一条）
    def record_daily_op(self, date: str, account: str, action: str,
                        status: str = "done", summary: str = "") -> None:
        """按 (date, account, action) 去重：同一天重复运行只更新这一条，不新增行。"""
        conn = self.connect()
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with conn:
            conn.execute(
                "INSERT INTO daily_ops(date,account,action,status,summary,created_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(date,account,action) DO UPDATE SET"
                " status=excluded.status, summary=excluded.summary,"
                " created_at=excluded.created_at",
                (date, account, action, status, summary, now))

    def daily_ops(self, account: str | None = None, limit: int = 30) -> pd.DataFrame:
        q = "SELECT * FROM daily_ops"
        args: tuple = ()
        if account:
            q += " WHERE account = ?"
            args = (account,)
        return pd.read_sql_query(q + " ORDER BY date DESC LIMIT ?",
                                 self.connect(), params=args + (limit,))

    def daily_backup(self) -> Path:
        """每日备份：复制 sqlite 到 backups/，保留最近 _KEEP_BACKUPS 份。

        文件名带账本 stem（ledger / ledger_2000），避免多账户互相覆盖。
        """
        self.connect().commit()
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d")
        dst = _BACKUP_DIR / f"{self.path.stem}_{stamp}.sqlite"
        shutil.copyfile(self.path, dst)
        backups = sorted(_BACKUP_DIR.glob(f"{self.path.stem}_*.sqlite"))
        for old in backups[:-_KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
        return dst
