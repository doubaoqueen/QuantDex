"""
QuantDex 本地可视化控制台（Python 标准库，零依赖）。

启动：python scripts/16_webapp.py
打开：http://127.0.0.1:8765

功能（MVP）：
  - 总览：大/小账户净值、现金、持仓数、待成交、涨跌家数
  - 持仓：代码/股数/现价/涨跌幅/当日盈亏/权重
  - 待成交订单：查看 + 取消（半自动调仓：人工可干预）
  - 手工下单：填代码/方向/数量，生成 pending 订单（T+1 开盘撮合）
  - 市场状态：沪深300 均线/回撤/宽度（红黄绿灯）
  - 日报：按日期查看
  - 运行：更新行情 / 跑流水线（后台，日志可看）
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR, board_limit
from src.data.market import get_kline
from src.data.snapshot import freshness as data_freshness
from src.data.snapshot import update_spot_snapshot
from src.data.storage import ParquetStore

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"      # Vue3 构建产物（存在则替代内嵌旧页面）
_MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".map": "application/json",
}


def _serve_static(rel: str):
    """安全地返回 frontend/dist 下的静态文件；不存在返回 None（回退旧页面）。"""
    base = FRONTEND_DIST.resolve()
    p = (base / rel.lstrip("/")).resolve()
    if not str(p).startswith(str(base)) or not p.is_file():
        return None
    ctype = _MIME.get(p.suffix.lower(), "application/octet-stream")
    return p.read_bytes(), ctype
from src.strategies.registry import STRATEGIES

# 账户定义唯一来源 = src/strategies/registry.py（D-039），本文件不再硬编码
ACCOUNTS = {k: {"db": STORE_DIR / "sim" / c["ledger"], "label": c["label"],
                "suffix": c["backtest_suffix"], "group": c["group"]}
            for k, c in STRATEGIES.items() if c.get("enabled")}
LOG = STORE_DIR / "logs" / "webapp_run.log"
AUDIT = STORE_DIR / "logs" / "webapp_audit.log"
START_LOG = STORE_DIR / "logs" / "webapp_starts.jsonl"   # 每次打开一行（时间戳/动作），D-045
STATE_FILE = STORE_DIR / "logs" / "webapp_state.json"    # 上一次自动更新/补日报的时间，做节流
AUTH_FILE = STORE_DIR / "webauth.json"      # D-037：控制台凭证（明文，等同 .env 待遇，勿入库）
SESSION_TTL = 7 * 24 * 3600
HOST, PORT = "127.0.0.1", 8765
# 重复启动节流（D-045）：一天开好几次服务，不应该把 949 只全量更新跑好几遍。
# 只节流"自动链"，手动点「更新行情」「跑流水线」永远放行；日志里会写明跳过的原因。
AUTO_UPDATE_COOLDOWN = 60 * 60          # 自动更新：两次之间至少 60 分钟
AUTO_REPORT_COOLDOWN = 20 * 60          # 自动补日报/邮件：失败重试间隔 20 分钟
LOG_MAX_BYTES = 2 * 1024 * 1024         # 运行日志超过 2MB 启动时轮转一份（只留 1 份 .1）
# R3-18 同源白名单：仍按 netloc 精确比对（防 http://127.0.0.1:8765.evil.com 前缀绕过），
# 但 localhost 与 127.0.0.1 是同一台机器——只认二者，用 localhost 打开时 POST 不再 403。
ALLOWED_ORIGINS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


def _auth_store() -> dict:
    """首次运行自动生成账号/密码（打印到控制台）；改密码=直接编辑该文件。"""
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    pw = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(10))
    st = {"user": "quantdex", "pass": pw, "secret": secrets.token_hex(32)}
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(st, indent=2), encoding="utf-8")
    print("")
    print(f"[auth] 控制台首次初始化  账号: {st['user']}  密码: {pw}")
    print(f"[auth] 凭证文件: {AUTH_FILE}（改密码=编辑该文件后重启）", flush=True)
    return st


AUTH = _auth_store()


def _verify_login(user: str, pw: str) -> bool:
    return hmac.compare_digest(str(user), AUTH["user"]) and         hmac.compare_digest(str(pw), AUTH["pass"])


def _make_token() -> str:
    exp = int(time.time()) + SESSION_TTL
    sig = hmac.new(AUTH["secret"].encode(), str(exp).encode(),
                   hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def _check_token(tok: str) -> bool:
    try:
        exp, sig = tok.split(".", 1)
        good = hmac.new(AUTH["secret"].encode(), exp.encode(),
                        hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:  # noqa: BLE001
        return False
_store = ParquetStore()
_cache = {"regime": (0.0, None)}
# busy：非子进程阶段的占用（快照更新线程），让 UI 能看到"正在跑"并阻止重复点击
_job = {"proc": None, "name": None, "busy": None}
_job_lock = threading.Lock()
_instance_lock = None      # 单实例锁句柄（保持打开=持锁）


def _db(acc):
    p = ACCOUNTS[acc]["db"]
    if not p.exists():
        return None
    return sqlite3.connect(p)


def _tail(df, n=1):
    return df.tail(n)


# ---------------------------------------------------------------- 启动日志与节流（D-045）
def _state() -> dict:
    """自动更新节流状态（小 JSON；读坏了当空，不影响服务）。"""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _set_state(**kw) -> None:
    st = _state()
    st.update(kw)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _note(msg: str) -> None:
    """往运行日志写一行带时间戳的说明（控制台「后台任务」面板可见）。"""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n[{time.strftime('%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _log_start(**rec) -> None:
    """每次打开服务记一行（只追加、可追溯；一行约 100~200 字节，一年几十 KB）。"""
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}
    try:
        START_LOG.parent.mkdir(parents=True, exist_ok=True)
        with START_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _report_missing(day: str | None) -> list:
    """当日（最近收盘交易日）还缺哪些账户的日报 —— 缺=邮件还没发。"""
    if not day:
        return []
    return [k for k in ACCOUNTS
            if not (STORE_DIR / "reports" / k / f"{day}.md").exists()]


def _rotate_log() -> None:
    """运行日志按大小轮转（只留 1 份 .1）：连跑几个月也不会无限长。"""
    try:
        if LOG.exists() and LOG.stat().st_size > LOG_MAX_BYTES:
            bak = LOG.with_name(LOG.name + ".1")
            bak.unlink(missing_ok=True)
            LOG.replace(bak)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 名称 / 行业 / 涨跌停
_META_CACHE = {"key": None, "names": {}, "industries": {}}


def _meta_maps():
    """symbol -> 名称 / 申万一级行业（meta 里现成，按文件 mtime 缓存，改文件自动重载）。"""
    try:
        p1 = STORE_DIR / "meta" / "bs_securities.parquet"   # 全量证券（含退市）→ 名称
        p2 = STORE_DIR / "meta" / "industry_sw.parquet"     # 申万一级 → 行业
        key = tuple(int(p.stat().st_mtime) if p.exists() else 0 for p in (p1, p2))
        if _META_CACHE["key"] == key and _META_CACHE["names"]:
            return _META_CACHE["names"], _META_CACHE["industries"]
        names, inds = {}, {}
        if p1.exists():
            df = pd.read_parquet(p1, columns=["symbol", "name"])
            names = dict(zip(df["symbol"].astype(str), df["name"].astype(str), strict=False))
        if p2.exists():
            df = pd.read_parquet(p2, columns=["symbol", "industry"])
            inds = dict(zip(df["symbol"].astype(str), df["industry"].astype(str), strict=False))
        _META_CACHE.update(key=key, names=names, industries=inds)
        return names, inds
    except Exception:  # noqa: BLE001
        return {}, {}


def _limit_tag(pct, symbol, name=None):
    """当日是否触及涨跌停（pct 为**小数**口径，与撮合同源：ST ±5%、创/科 ±20%、主板 ±10%）。"""
    if pct is None:
        return None
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return None
    if p != p:  # NaN
        return None
    lim = board_limit(symbol, name)
    if p >= lim - 1e-3:
        return "涨停"
    if p <= -lim + 1e-3:
        return "跌停"
    return None


def _with_names(records: list) -> list:
    """给 trade_log 这类行补中文名称（就地写 name 字段，前端直接显示）。"""
    names, _ = _meta_maps()
    for r in records:
        r["name"] = names.get(str(r.get("symbol")), "")
    return records


def overview(acc):
    conn = _db(acc)
    if conn is None:
        return {"account": acc, "empty": True}
    try:
        snap = pd.read_sql_query("SELECT * FROM account_snapshot ORDER BY date", conn)
        pend = pd.read_sql_query("SELECT * FROM trade_log WHERE status='pending'", conn)
        pos = pd.read_sql_query("SELECT * FROM positions WHERE date=(SELECT MAX(date) FROM positions)", conn)
        last_date = str(snap["date"].iloc[-1]) if len(snap) else None
        prior = snap[pd.to_datetime(snap["date"]) < pd.Timestamp(last_date)] if last_date else snap
        out = {"account": acc, "label": ACCOUNTS[acc]["label"], "empty": False,
               "date": last_date,
               "nav": float(snap["total_nav"].iloc[-1]) if len(snap) else None,
               "cash": float(snap["cash"].iloc[-1]) if len(snap) else None,
               "mv": float(snap["market_value"].iloc[-1]) if len(snap) else None,
               "prev_nav": float(prior["total_nav"].iloc[-1]) if len(prior) else None,
               "base_nav": float(snap["total_nav"].iloc[0]) if len(snap) else None,
               "positions": int(len(pos)), "pending": int(len(pend))}
        out["day_pnl"] = (out["nav"] - out["prev_nav"]) if out["prev_nav"] else 0.0
        out["cum_ret"] = (out["nav"] / out["base_nav"] - 1.0) if out["base_nav"] else 0.0
        # D-043：累计交易代价常驻账户卡（让每次看账户都能感到费用）
        try:
            agg = pd.read_sql_query(
                "SELECT COUNT(*) n, COALESCE(SUM(amount),0) amt, "
                "COALESCE(SUM(fee),0) commission, COALESCE(SUM(stamp_duty),0) stamp "
                "FROM trade_log WHERE status='filled'", _db(acc))
            if agg["n"].iloc[0]:
                amt = float(agg["amt"].iloc[0])
                slip = amt * 0.0005
                commission = float(agg["commission"].iloc[0])
                stamp = float(agg["stamp"].iloc[0])
                out["fee"] = {"n": int(agg["n"].iloc[0]), "turnover": amt,
                              "commission": commission, "stamp": stamp, "slip": slip,
                              "total": commission + stamp + slip}
        except Exception as e:  # noqa: BLE001
            print(f"[overview] 费用汇总失败: {e}", flush=True)
        return out
    finally:
        conn.close()


def positions(acc):
    conn = _db(acc)
    if conn is None:
        return []
    try:
        last = pd.read_sql_query("SELECT MAX(date) d FROM positions", conn)["d"].iloc[0]
        pos = pd.read_sql_query("SELECT * FROM positions WHERE date=?", conn, params=(last,))
    finally:
        conn.close()
    names, inds = _meta_maps()
    rows = []
    for r in pos.itertuples(index=False):
        sym = str(r.symbol)
        prev, pct_chg = None, None
        try:
            k = get_kline(sym, adjust="qfq", store=_store)
            k = k[pd.to_datetime(k["date"]) <= pd.Timestamp(last)]
            if len(k) >= 2:
                prev = float(k["close"].iloc[-2])
            if len(k) and "pct_chg" in k.columns:
                # 仓库里的 pct_chg 是百分数（-1.39 表示 -1.39%），这里统一换成分数口径
                pct_chg = float(k["pct_chg"].iloc[-1]) / 100.0
        except Exception:  # noqa: BLE001
            pass
        px = float(r.close)
        chg = (px / prev - 1) if prev else None
        pnl = (px - prev) * float(r.shares) if prev else 0.0
        rows.append({"symbol": sym, "name": names.get(sym, ""),
                     "industry": inds.get(sym, ""), "shares": float(r.shares),
                     "close": px, "pct": chg, "pnl": pnl,
                     "weight": float(r.weight), "limit": _limit_tag(pct_chg, sym, names.get(sym))})
    return rows


def nav_history(acc):
    """净值历史（快照全表）：date/total_nav/cash/benchmark_nav，供前端画净值曲线。"""
    conn = _db(acc)
    if conn is None:
        return {"rows": []}
    try:
        df = pd.read_sql_query(
            "SELECT date, total_nav, cash, benchmark_nav FROM account_snapshot ORDER BY date",
            conn)
        return {"rows": df.to_dict("records")}
    finally:
        conn.close()


def trades(acc, limit=30):
    """最近成交/订单记录（含 pending/filled/rejected/cancelled），供前端订单面板。"""
    conn = _db(acc)
    if conn is None:
        return {"rows": []}
    try:
        df = pd.read_sql_query(
            "SELECT fill_date, symbol, side, qty, price, status FROM trade_log "
            "ORDER BY id DESC LIMIT ?", conn, params=(int(limit),))
        return {"rows": _with_names(df.to_dict("records"))}
    finally:
        conn.close()


def rebalance_diff(acc):
    """调仓审批视图：待成交订单 -> 当前权重 vs 调整后权重 对比。

    目标权重按订单参考价（信号日收盘）近似：市值 = 持仓股数×最新收盘 ± 订单金额。
    """
    conn = _db(acc)
    if conn is None:
        return {"rows": [], "nav": None}
    try:
        pend = pd.read_sql_query(
            "SELECT id, symbol, side, qty, price, amount, fill_date FROM trade_log "
            "WHERE status='pending' ORDER BY symbol", conn)
        last_pos = pd.read_sql_query(
            "SELECT symbol, shares, close FROM positions "
            "WHERE date=(SELECT MAX(date) FROM positions)", conn)
        snap = pd.read_sql_query(
            "SELECT total_nav FROM account_snapshot ORDER BY date DESC LIMIT 1", conn)
    finally:
        conn.close()
    nav = float(snap["total_nav"].iloc[0]) if len(snap) else None
    if pend.empty or not nav:
        return {"rows": [], "nav": nav}
    mv = {str(r.symbol): float(r.shares) * float(r.close)
          for r in last_pos.itertuples(index=False)}
    names, _inds = _meta_maps()
    rows = []
    for o in pend.itertuples(index=False):
        sym = str(o.symbol)
        cur_mv = mv.get(sym, 0.0)
        delta = float(o.amount) if o.side == "buy" else -float(o.amount)
        tgt_mv = max(cur_mv + delta, 0.0)
        rows.append({
            "id": int(o.id), "symbol": sym, "name": names.get(sym, ""), "side": o.side,
            "qty": float(o.qty), "price": float(o.price), "fill_date": o.fill_date,
            "cur_weight": cur_mv / nav,
            "tgt_weight": tgt_mv / nav,
            "delta_weight": (tgt_mv - cur_mv) / nav,
        })
    return {"rows": rows, "nav": nav}


def school_data():
    """量化学院动态数据（D-044：内容数据在 src/analysis/school_content，统计实时算）。"""
    from src.analysis import expr_factors as EF
    from src.analysis.factors import FACTOR_NAMES
    from src.analysis.school_content import OPS_TABLE, PITFALLS, STATUS_STYLE, STRATEGY_MAP
    st = {"n_factors": len(FACTOR_NAMES) + len(EF.EXPR_FACTORS) + 1,
          "n_expr": len(EF.EXPR_FACTORS)}
    try:
        reg = pd.read_parquet(STORE_DIR / "registry.parquet")
        g = reg.groupby("dataset").agg(n=("name", "count"))
        st["daily_syms"] = int(g.loc["daily", "n"]) if "daily" in g.index else 0
        st["total_rows"] = f"{int(reg['rows'].sum()) / 1e4:.0f} 万"
        st["datasets"] = int(len(reg))
    except Exception:  # noqa: BLE001
        st.update({"daily_syms": 0, "total_rows": "-", "datasets": 0})
    exp_dir = STORE_DIR / "experiments"
    st["experiments"] = len(list(exp_dir.glob("*.json"))) if exp_dir.exists() else 0
    ic = []
    pth = STORE_DIR / "factor_results" / "expr_ic_summary.parquet"
    if pth.exists():
        df = pd.read_parquet(pth)
        for _, r in df.head(18).iterrows():
            ic.append({"factor": r["factor"], "family": r["family"],
                       "ic20": round(float(r["ic20"]), 4),
                       "t20": round(float(r["t20"]), 2),
                       "sig": bool(abs(r["t20"]) > 2),
                       "coverage": round(float(r["coverage"]), 2),
                       "desc": EF.EXPR_FACTORS.get(r["factor"], {}).get("desc", "")})
    st["ic_rows"] = ic
    st["ops_rows"] = [{"op": o, "desc": d, "eg": e} for o, d, e in OPS_TABLE]
    st["pitfalls"] = [{"title": t, "desc": d} for t, d in PITFALLS]
    st["strategies"] = [{**sm, "style": STATUS_STYLE[sm["status"]][0]}
                        for sm in STRATEGY_MAP]
    accounts = []
    for k, c in ACCOUNTS.items():
        conn = _db(k)
        nav = None
        if conn is not None:
            try:
                sn = pd.read_sql_query(
                    "SELECT total_nav FROM account_snapshot ORDER BY date DESC LIMIT 1",
                    conn)
                nav = float(sn["total_nav"].iloc[0]) if len(sn) else None
            finally:
                conn.close()
        accounts.append({"key": k, "label": c["label"], "nav": nav})
    st["accounts"] = accounts
    return st


def fees(acc):
    """费用统计（影子盘真实账本，仅已成交订单）。

    fee 列 = 佣金(含最低5元) + 过户费（pipeline._trade_fee 合并记录）；
    印花税单独一列；滑点不入库，按成交额 × 0.0005 估算（与撮合口径一致）。
    """
    conn = _db(acc)
    if conn is None:
        return {"summary": None, "recent": []}
    try:
        df = pd.read_sql_query(
            "SELECT fill_date, symbol, side, qty, price, amount, fee, stamp_duty "
            "FROM trade_log WHERE status='filled' ORDER BY id", conn)
    finally:
        conn.close()
    if df.empty:
        return {"summary": None, "recent": []}
    slip = float(df["amount"].sum()) * 0.0005
    summary = {
        "n_filled": int(len(df)),
        "turnover": float(df["amount"].sum()),
        "commission": float(df["fee"].sum()),
        "stamp": float(df["stamp_duty"].sum()),
        "slip_est": slip,
        "total": float(df["fee"].sum()) + float(df["stamp_duty"].sum()) + slip,
        "first_date": str(df["fill_date"].iloc[0]),
        "last_date": str(df["fill_date"].iloc[-1]),
    }
    recent = df.tail(10).iloc[::-1].to_dict("records")
    return {"summary": summary, "recent": recent}


_CAND_CACHE = {}          # acc -> (时间戳, 结果)；按账户缓存，切账户不用重算全池


def candidates(acc):
    """候选池（海选）：该账户策略在最新交易日对全部 PIT 成分打分的前 15 名。

    分数与流水线同口径（截面 rank 合成）；标注持仓中/待成交/备选#N，并带名称/行业/涨跌停。
    为速度只取每标的最近 420 行计算（滚动窗口足够），结果按账户缓存 15 分钟。
    """
    import time as _t
    now = _t.time()
    hit = _CAND_CACHE.get(acc)
    if hit and now - hit[0] < 900:
        return hit[1]
    import numpy as np

    from src.analysis.factors import compute_raw_factors
    from src.data import financials, universe
    from src.data.market import get_kline

    store = _store
    is_div = (acc == "div")
    symbols = universe.pit_union_symbols("000300", store)
    div_by = {}
    if is_div:
        try:
            dpanel = financials.load_dividend_panel(store, symbols)
            dpanel["date"] = pd.to_datetime(dpanel["date"])
            div_by = {str(k): g for k, g in dpanel.groupby("symbol")}
        except Exception:  # noqa: BLE001
            is_div = False
    recs = []
    for sym in symbols:
        try:
            k = get_kline(str(sym), adjust="qfq", store=store)
        except Exception:  # noqa: BLE001
            continue
        if k is None or len(k) < 70:
            continue
        raw = compute_raw_factors(k.tail(420).reset_index(drop=True))
        last = raw.iloc[-1]
        if not all(np.isfinite(last[c]) for c in ("turnover_20", "vol_20", "rev_5")):
            continue
        rec = {"symbol": str(sym), "close": float(last["close"]),
               "turnover_20": float(last["turnover_20"]),
               "vol_20": float(last["vol_20"]), "rev_5": float(last["rev_5"])}
        try:   # 当日涨跌幅：仓库里是百分数，统一换成小数口径（与撮合/涨跌停判断一致）
            rec["pct_chg"] = float(k["pct_chg"].iloc[-1]) / 100.0
        except (KeyError, TypeError, ValueError):
            rec["pct_chg"] = float("nan")
        if is_div:
            g = div_by.get(str(sym))
            if g is None:
                continue
            sdiv = g.groupby("date")["dividend"].sum().sort_index()
            daily = sdiv.reindex(pd.DatetimeIndex(raw["date"])).fillna(0.0)
            ttm = float(daily.rolling("365D").sum().iloc[-1])
            if not ttm > 0:
                continue
            rec["dy_ttm"] = ttm / rec["close"]
        recs.append(rec)

    df = pd.DataFrame(recs)
    for c in ("turnover_20", "vol_20", "rev_5"):
        df[c] = df[c].rank(pct=True)
    if is_div:
        df["dy_ttm"] = df["dy_ttm"].rank(pct=True)
        df["score"] = ((1 - df["turnover_20"]) + (1 - df["vol_20"]) + df["dy_ttm"]) / 3
    else:
        df["score"] = ((1 - df["turnover_20"]) + (1 - df["vol_20"]) + (1 - df["rev_5"])) / 3
    df = df.sort_values("score", ascending=False).head(15).reset_index(drop=True)

    held, pend, cash = set(), set(), None
    conn = _db(acc)
    if conn is not None:
        try:
            pos = pd.read_sql_query(
                "SELECT symbol FROM positions WHERE date=(SELECT MAX(date) FROM positions)", conn)
            held = set(pos["symbol"].astype(str))
            po = pd.read_sql_query(
                "SELECT symbol FROM trade_log WHERE status='pending'", conn)
            pend = set(po["symbol"].astype(str))
            sn = pd.read_sql_query(
                "SELECT cash FROM account_snapshot ORDER BY date DESC LIMIT 1", conn)
            cash = float(sn["cash"].iloc[0]) if len(sn) else None
        finally:
            conn.close()
    try:
        k0 = get_kline("000300", adjust="none", store=store)
        latest = str(pd.to_datetime(k0["date"]).max().date())
    except Exception:  # noqa: BLE001
        latest = None
    rows = []
    names, inds = _meta_maps()
    for i, r in df.iterrows():
        sym = str(r["symbol"])
        chg = r.get("pct_chg")
        try:
            chg = float(chg)
        except (TypeError, ValueError):
            chg = None
        status = ("持仓中" if sym in held
                  else "待成交" if sym in pend else f"备选 #{i + 1}")
        rows.append({"rank": i + 1, "symbol": sym,
                     "name": names.get(sym, ""), "industry": inds.get(sym, ""),
                     "score": round(float(r["score"]), 4),
                     "close": round(float(r["close"]), 2),
                     "pct": (round(chg, 4) if chg is not None else None), "limit": _limit_tag(chg, sym, names.get(sym)),
                     "lot_cost": round(float(r["close"]) * 100, 0),
                     "status": status})
    out = {"strategy": ("红利版：低换手+低波+股息率" if is_div else
                        "现行版：低换手+低波+反转"),
           "date": latest, "cash": cash, "rows": rows}
    _CAND_CACHE[acc] = (now, out)
    return out


def position_alerts(acc):
    """持仓预警：距持有期最高收盘回撤 <= -10% 的标的。

    仅提醒、不自动处置——处置类规则（减半/清仓/条件式）已被回测否定（D-026）：
    该策略属低波/低换手横截面组合，个股止损系统性卖在低点、回撤却不改善。
    """
    conn = _db(acc)
    if conn is None:
        return {"rows": []}
    try:
        pos = pd.read_sql_query(
            "SELECT symbol, shares, close FROM positions "
            "WHERE date=(SELECT MAX(date) FROM positions)", conn)
        first = pd.read_sql_query(
            "SELECT symbol, MIN(date) AS first_date FROM positions GROUP BY symbol", conn)
    finally:
        conn.close()
    first_map = {str(r.symbol): pd.Timestamp(r.first_date)
                 for r in first.itertuples(index=False)}
    rows = []
    for r in pos.itertuples(index=False):
        sym = str(r.symbol)
        try:
            k = get_kline(sym, adjust="qfq", store=_store)
            s = pd.Series(k["close"].astype(float).values,
                          index=pd.to_datetime(k["date"])).sort_index()
            since = s[s.index >= first_map.get(sym, s.index[0])]
            if len(since) < 2:
                continue
            peak, last = float(since.max()), float(since.iloc[-1])
            dd = last / peak - 1.0
            if dd <= -0.10:
                rows.append({"symbol": sym, "close": last, "peak": peak,
                             "dd_peak": dd,
                             "held_since": str(first_map.get(sym, pd.NaT))[:10],
                             "level": "重度" if dd <= -0.15 else "预警"})
        except Exception:  # noqa: BLE001
            continue
    rows.sort(key=lambda x: x["dd_peak"])
    return {"rows": rows}


def backtest(acc="large"):
    """回测/样本外结果（05/06 的 parquet 产物 + 基准），供前端回测面板。

    账户 div 对应红利版产物（*_div 后缀，05/06 以 dividend 策略生成），其余为现行版。"""
    import numpy as np

    try:
        bench = _store.read("daily", "000300__none").set_index("date")["close"].astype(float)
        bench.index = pd.to_datetime(bench.index)
        bench = bench.sort_index()

        def _nav(path):
            df = pd.read_parquet(path)
            if "date" in df.columns:
                df = df.set_index("date")
            s = df["nav"].astype(float).sort_index()
            s.index = pd.to_datetime(s.index)
            return s

        def _stats(nav):
            r = nav.pct_change().dropna()
            years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
            total = float(nav.iloc[-1] / nav.iloc[0] - 1)
            b = bench.reindex(nav.index).ffill().dropna()
            bt = float(b.iloc[-1] / b.iloc[0] - 1) if len(b) else None
            return {"total": total,
                    "ann": float((1 + total) ** (1 / years) - 1),
                    "mdd": float((nav / nav.cummax() - 1).min()),
                    "sharpe": float(r.mean() / r.std() * 250 ** 0.5) if r.std() > 0 else None,
                    "bench_total": bt,
                    "excess": (total - bt) if bt is not None else None,
                    "start": str(nav.index[0].date()), "end": str(nav.index[-1].date())}

        def _pack(nav):
            b = bench[(bench.index >= nav.index[0]) & (bench.index <= nav.index[-1])]
            bn = (b / b.iloc[0]).reindex(nav.index).ffill()
            return {"dates": [str(d.date()) for d in nav.index],
                    "nav": [round(float(v), 4) for v in nav.values],
                    "bench": [round(float(v), 4) for v in bn.values],
                    "stats": _stats(nav)}

        # 账户 -> 回测产物映射（D-032）：
        #   large=现行版；topk=TopkDropout 版(_tk)；div=红利版(_div)；
        #   small 无独立回测（3只整手执行验证组合）——展示现行版并明确标注"参考"
        suffix = ACCOUNTS.get(acc, {}).get("suffix", "")
        name = ("红利版：低换手+低波+股息率" if suffix == "_div" else
                "TopkDropout 版：同现行因子+换手上界（n_drop=5/期）" if suffix == "_tk" else
                "现行版：低换手+低波+反转")
        if acc == "small":
            name = "参考：现行版（小账户为 3 只整手执行验证，无独立历史回测）"
        out = {"strategy": name,
               "full": _pack(_nav(STORE_DIR / "portfolio_results" / f"nav_000300_top50_reb20{suffix}.parquet")),
               "oos": _pack(_nav(STORE_DIR / "walkforward_results" / f"nav_oos_000300{suffix}.parquet"))}
        out["oos_start"] = out["oos"]["dates"][0]

        nav_full = pd.Series(out["full"]["nav"], index=pd.to_datetime(out["full"]["dates"]))
        sy = {}
        for y, g in nav_full.groupby(nav_full.index.year):
            if len(g) > 2:
                sy[int(y)] = float(g.iloc[-1] / g.iloc[0] - 1)
        by = {int(y): float((g / g.iloc[0] - 1).iloc[-1])
              for y, g in bench.groupby(bench.index.year)}
        out["yearly"] = [{"year": y, "strategy": sy.get(y), "bench": by.get(y)}
                         for y in sorted(set(sy) | set(by))]
        fp = STORE_DIR / "walkforward_results" / f"folds_000300{suffix}.parquet"
        if fp.exists():
            def _jsonable(v):
                if v is None:
                    return None
                if hasattr(v, "tolist"):
                    return v.tolist()
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return str(v)
            out["folds"] = [{k: _jsonable(v) for k, v in r.items()}
                            for r in pd.read_parquet(fp).to_dict("records")]
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def orders(acc):
    conn = _db(acc)
    if conn is None:
        return []
    try:
        df = pd.read_sql_query(
            "SELECT id,symbol,side,qty,price,fill_date,status,note FROM trade_log "
            "WHERE status='pending' ORDER BY id", conn)
        return _with_names(df.to_dict("records"))
    finally:
        conn.close()


def symbol_lookup(symbol):
    """手工下单时边输代码边看标的：名称/行业/最新价/涨跌停。"""
    sym = str(symbol or "").strip()
    if not (len(sym) == 6 and sym.isdigit()):
        return {"err": "代码须为 6 位数字"}
    names, inds = _meta_maps()
    out = {"symbol": sym, "name": names.get(sym, ""), "industry": inds.get(sym, "")}
    try:
        k = get_kline(sym, adjust="qfq", store=_store)
        if k is None or not len(k):
            out["err"] = f"没有 {sym} 的行情数据"
            return out
        out["close"] = round(float(k["close"].iloc[-1]), 2)
        out["date"] = str(pd.to_datetime(k["date"]).max().date())
        pct = k["pct_chg"].iloc[-1] if "pct_chg" in k.columns else None
        if pct is not None and pd.notna(pct):
            pct = float(pct) / 100.0        # 仓库里是百分数 -> 小数口径
            out["pct"] = round(pct, 4)
            out["limit"] = _limit_tag(pct, sym, out.get("name"))
    except Exception:  # noqa: BLE001
        out["err"] = f"没有 {sym} 的行情数据"
    return out


def cancel_order(acc, order_id):
    conn = _db(acc)
    if conn is None:
        return {"ok": False, "msg": "账户不存在"}
    try:
        cur = conn.execute(
            "UPDATE trade_log SET status='cancelled', note=note || '（人工取消）' "
            "WHERE id=? AND status='pending'", (int(order_id),))
        conn.commit()
        # 审计留痕由调用层 _audit 记录
        return {"ok": cur.rowcount > 0, "msg": "已取消" if cur.rowcount else "订单不存在或非 pending"}
    finally:
        conn.close()


def add_order(acc, symbol, side, qty, price=None):
    symbol = str(symbol).strip()
    if side not in ("buy", "sell"):
        return {"ok": False, "msg": "方向只能是 buy/sell"}
    try:
        qty = int(float(qty))
    except (TypeError, ValueError):
        return {"ok": False, "msg": "数量必须是数字"}
    if qty <= 0 or qty % 100 != 0:
        return {"ok": False, "msg": "数量必须是 100 的整数倍"}
    try:
        k = get_kline(symbol, adjust="qfq", store=_store)
    except Exception:  # noqa: BLE001
        return {"ok": False, "msg": f"没有 {symbol} 的行情数据"}
    import math as _math
    try:
        px = float(price) if price else float(k["close"].iloc[-1])
    except (TypeError, ValueError):
        return {"ok": False, "msg": "价格非法"}
    last_px = float(k["close"].iloc[-1])
    if not _math.isfinite(px) or px <= 0 or px > last_px * 1.25:
        return {"ok": False, "msg": f"价格非法（须为正数且不超过现价 125%: {last_px:.2f}）"}
    import pandas as pd2
    cal = pd2.read_parquet(STORE_DIR / "meta" / "trade_calendar.parquet")
    cal = pd2.to_datetime(cal["date"]).sort_values()
    # R3-08：基点=账户最新快照日与个股行情末日的较大者再取下一交易日
    # （停牌股按自身末日推算会落过去/永 pending）
    conn0 = _db(acc)
    snap_day = None
    if conn0 is not None:
        try:
            sd = pd2.read_sql_query(
                "SELECT MAX(date) d FROM account_snapshot", conn0)
            snap_day = pd2.to_datetime(sd["d"].iloc[0]) if len(sd) and sd["d"].iloc[0] else None
        finally:
            conn0.close()
    last = max(pd2.to_datetime(k["date"]).max(),
               snap_day) if snap_day is not None else pd2.to_datetime(k["date"]).max()
    nxt = cal[cal > last]
    fill = str(nxt.iloc[0].date()) if len(nxt) else str((last + pd2.Timedelta(days=1)).date())
    conn = _db(acc)
    if conn is None:
        return {"ok": False, "msg": "账户不存在"}
    try:
        amount = px * qty
        fee = max(amount * 0.00025, 5.0) + amount * 0.00001
        # 资金/持仓校验（D-034，外部评审指出）：买查现金（扣已挂买单占用），卖查持仓（扣已挂卖单）
        snap = pd.read_sql_query(
            "SELECT cash FROM account_snapshot ORDER BY date DESC LIMIT 1", conn)
        cash = float(snap["cash"].iloc[0]) if len(snap) else 0.0
        pend = pd.read_sql_query(
            "SELECT symbol, side, amount, qty FROM trade_log WHERE status='pending'", conn)
        if side == "buy":
            committed = float(pend[pend["side"] == "buy"]["amount"].sum()) if len(pend) else 0.0
            need = amount + fee + amount * 0.0005
            if need > cash - committed + 1e-6:
                return {"ok": False,
                        "msg": f"现金不足：需 {need:,.0f} 元，可用 {cash - committed:,.0f} 元（含已挂单占用）"}
        else:
            held = pd.read_sql_query(
                "SELECT shares FROM positions WHERE symbol=? "
                "AND date=(SELECT MAX(date) FROM positions)", conn, params=(symbol,))
            have = float(held["shares"].iloc[0]) if len(held) else 0.0
            committed_q = float(pend[(pend["side"] == "sell") &
                                     (pend["symbol"] == symbol)]["qty"].sum()) if len(pend) else 0.0
            if qty > have - committed_q + 1e-6:
                return {"ok": False,
                        "msg": f"持仓不足：卖 {qty} 股，可用 {have - committed_q:.0f} 股（含已挂卖单）"}
        conn.execute(
            "INSERT INTO trade_log(signal_date,fill_date,symbol,side,qty,price,amount,"
            "fee,stamp_duty,status,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(last.date()), fill, symbol, side, qty, px, amount,
             fee, amount * 0.0005 if side == "sell" else 0.0, "pending", "人工下单"))
        conn.commit()
        return {"ok": True, "msg": f"已挂单：{symbol} {side} {qty} 股，成交日 {fill}",
                "_audit": f"add_order {acc} {symbol} {side} {qty}"}
    finally:
        conn.close()


def regime():
    now = time.time()
    if _cache["regime"][1] is not None and now - _cache["regime"][0] < 600:
        return _cache["regime"][1]
    b = _store.read("daily", "000300__none").set_index("date")["close"].astype(float)
    b.index = pd.to_datetime(b.index)
    b = b.sort_index()
    last = float(b.iloc[-1])
    ma20 = float(b.rolling(20).mean().iloc[-1])
    ma60 = float(b.rolling(60).mean().iloc[-1])
    ma200 = float(b.rolling(200).mean().iloc[-1])
    peak = float(b.iloc[-250:].max())
    out = {"date": str(b.index[-1].date()), "close": last, "ma20": ma20,
           "ma60": ma60, "ma200": ma200,
           "ret20": float(last / b.iloc[-21] - 1),
           "ret60": float(last / b.iloc[-61] - 1),
           "ret120": float(last / b.iloc[-121] - 1),
           "dd": float(last / peak - 1),
           "above20": last > ma20, "above60": last > ma60, "above200": last > ma200,
           "breadth": None}
    try:
        import glob

        import numpy as np
        files = glob.glob(os.path.join(str(STORE_DIR / "market" / "daily"), "*.parquet"))[:200]
        above = tot = 0
        for p in files:
            df = pd.read_parquet(p, columns=["date", "close"])
            if len(df) < 70:
                continue
            c = df["close"].astype(float).sort_index()
            tot += 1
            if c.iloc[-1] > c.rolling(60).mean().iloc[-1]:
                above += 1
        out["breadth"] = above / tot if tot else None
    except Exception:  # noqa: BLE001
        pass
    _cache["regime"] = (now, out)
    return out


def reports(acc):
    d = STORE_DIR / "reports" / acc
    if not d.exists():
        return []
    return sorted([p.stem for p in d.glob("*.md")], reverse=True)


def report_text(acc, date):
    """读日报文本。account 必须在注册表内、date 必须是 YYYY-MM-DD——防路径遍历（D-034）。"""
    import re as _re
    if acc not in ACCOUNTS:
        return "未找到日报（账户不存在）"
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date or "")):
        return "未找到日报（日期非法）"
    p = STORE_DIR / "reports" / acc / f"{date}.md"
    return p.read_text(encoding="utf-8") if p.exists() else "未找到日报"


def _job_cmd(name: str) -> list:
    """任务名 -> 命令行。D-045：把"只更新行情"和"跑完整每日流程（含发信）"分开。"""
    py = sys.executable
    daily = [py, str(ROOT / "scripts" / "10_daily_run.py")]
    return {
        # 只更新行情（手动「更新行情」用；不发信、不跑流水线）
        "update": [py, str(ROOT / "scripts" / "02b_update_pit.py")],
        # 完整每日流程：更新行情 + 四账户流水线 + 合并日报邮件（自动链用）
        "daily": daily,
        # 数据已是最新，只补流水线 + 合并日报邮件（自动链用）
        "report": daily + ["--skip-update"],
        # 手动「跑流水线」：不发信（避免手动调试惊动收件箱）
        "pipeline": daily + ["--skip-update", "--no-push"],
    }.get(name, daily + ["--skip-update", "--no-push"])


def start_job(name):
    with _job_lock:                                   # R3-21：双击竞态
        if _job["proc"] is not None and _job["proc"].poll() is None:
            return {"ok": False, "msg": f"已有任务在跑：{_job['name']}"}
        import subprocess
        cmd = _job_cmd(name)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        f = open(LOG, "a", encoding="utf-8")
        f.write(chr(10) + "===== " + time.strftime('%Y-%m-%d %H:%M:%S') + " " + name + " =====")
        f.flush()
        # 子进程强制 UTF-8：否则它按 GBK 写进 UTF-8 日志文件，面板里全是乱码
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        _job["proc"] = subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=f, env=env)
        _job["name"] = name
        return {"ok": True, "msg": f"已启动：{name}"}


def job_status():
    p = _job["proc"]
    busy = _job["busy"]
    running = bool(busy) or (p is not None and p.poll() is None)
    tail = ""
    if LOG.exists():
        lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(lines[-25:])
    return {"running": running, "name": busy or _job["name"], "log": tail}


def freshness_info():
    try:
        f = data_freshness(_store)
        f["missing_reports"] = _report_missing(f.get("last_closed"))
        ts = _state().get("last_auto_update")
        f["last_auto"] = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else None
        return f
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "stale": True}


def ops(acc, limit=14):
    conn = _db(acc)
    if conn is None:
        return []
    try:
        df = pd.read_sql_query(
            "SELECT date,action,status,summary FROM daily_ops ORDER BY date DESC LIMIT ?",
            conn, params=(limit,))
        return df.to_dict("records")
    except Exception:  # noqa: BLE001
        return []
    finally:
        conn.close()


def _spot_work(auto=False):
    """先更新指数（基准），再快照股票；失败则回退 baostock 补齐。

    auto=True（打开服务触发的自动链，D-045）时顺带把当日日报+邮件补齐：
      - 快照成功 → 当日日报缺失则只跑流水线+发信（report）
      - 快照失败 → 跑完整每日流程（更新行情+流水线+发信，daily）
    手动点「更新行情」auto=False：只更新行情，不发信（保持原行为）。
    """
    from src.data.snapshot import update_indexes
    try:
        try:
            update_indexes(_store)
        except Exception as e:  # noqa: BLE001
            _note(f"[index] 基准指数更新失败（不影响个股快照）：{e}")
        fed = None
        try:
            r = update_spot_snapshot(_store)
            if r.get("ok"):
                fed = r.get("date")
            elif "盘中" in str(r.get("reason", "")):
                _note("[spot] 盘中，收盘前不落库；收盘后再打开会自动更新")
                return
        except Exception as e:  # noqa: BLE001
            _note(f"[spot] 东财快照源不可用（{e}），自动改用 baostock 逐只补齐")
        if not auto:
            if fed:
                return
            jr = start_job("update")
            if not jr.get("ok"):
                _note(f"[spot] 快照未取到数据，且补齐任务未启动：{jr.get('msg')}")
            return
        # ---- 自动链：把当日日报/邮件也补齐 ----
        if fed:
            miss = _report_missing(fed)
            if miss:
                _note(f"[auto] 行情已更新到 {fed}，但日报缺失（{'/'.join(miss)}）"
                      f"→ 跑流水线并合并发信")
                _set_state(last_report_update=time.time())
                start_job("report")
            else:
                _note("[auto] 行情已更新且当日日报齐全，无需再跑流水线")
        else:
            _note("[auto] 快照不可用 → 跑完整每日流程（更新行情 + 四账户流水线 + 合并日报邮件）")
            start_job("daily")
    finally:
        _job["busy"] = None


def start_spot_update(auto=False):
    with _job_lock:                                   # 防止连点开出一堆线程
        running = (_job["proc"] is not None and _job["proc"].poll() is None)
        if _job["busy"] or running:
            return {"ok": False, "msg": f"已有任务在跑：{_job['busy'] or _job['name']}"}
        _job["busy"] = "spot"
    threading.Thread(target=_spot_work, args=(auto,), daemon=True).start()
    return {"ok": True, "msg": "已开始检查并更新行情（快照优先，失败自动回退 baostock）"}


def auto_update_on_start():
    """打开服务时的自动链（D-045）：记启动时间戳 + 节流，别一天开几次就跑几次。"""
    try:
        f = freshness_info()
        day = f.get("last_closed")
        stale = bool(f.get("stale"))
        miss = f.get("missing_reports") or []
        _log_start(event="startup", stale=stale, up_to_date=f.get("up_to_date"),
                   sample=f.get("sample"), last_closed=day, missing_reports=miss)
        now = time.time()
        st = _state()
        if stale:
            last = float(st.get("last_auto_update") or 0)
            left = AUTO_UPDATE_COOLDOWN - (now - last)
            if last and left > 0:
                _note(f"[startup] 距上次自动更新仅 {int((now - last) / 60)} 分钟，本次跳过"
                      f"（{int(left / 60)} 分钟后才会再自动补；急用请点「更新行情」）")
                _log_start(event="skip-cooldown", reason="stale",
                           minutes=int((now - last) / 60))
                return
            _set_state(last_auto_update=now)
            _log_start(event="auto-update")
            start_spot_update(auto=True)
            return
        if miss:
            last = float(st.get("last_report_update") or 0)
            left = AUTO_REPORT_COOLDOWN - (now - last)
            if last and left > 0:
                _note(f"[startup] {day} 日报缺失（{'/'.join(miss)}），但 {int((now - last) / 60)} "
                      f"分钟前刚补过，本次跳过（{int(left / 60)} 分钟后重试）")
                _log_start(event="skip-cooldown", reason="report", missing_reports=miss)
                return
            _set_state(last_report_update=now)
            _note(f"[startup] 数据已最新，但 {day} 的日报缺失（{'/'.join(miss)}）"
                  f"→ 跑流水线并合并发信")
            _log_start(event="catch-up-report", missing_reports=miss)
            start_job("report")
            return
        _note(f"[startup] 数据已最新（{f.get('up_to_date')}/{f.get('sample')}）、"
              f"当日日报齐全，无需后台任务")
        _log_start(event="nothing-to-do")
    except Exception as e:  # noqa: BLE001
        _note(f"[startup] 自动检查失败（不影响使用）：{e}")


PAGE = ""  # 旧内嵌页面已移除（dist 随 git 提交，见 D-034）


LOGIN_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>QuantDex 控制台 · 登录</title><style>
body{margin:0;font-family:"Microsoft YaHei",system-ui;background:#0f172a;color:#e2e8f0;
     display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:34px 38px;width:320px}
h1{font-size:18px;margin:0 0 6px}.sub{color:#64748b;font-size:12px;margin-bottom:20px}
input{width:100%;box-sizing:border-box;padding:10px 12px;margin-bottom:12px;border:1px solid #334155;
      border-radius:8px;background:#0f172a;color:#e2e8f0;font-size:14px}
input:focus{outline:none;border-color:#38bdf8}
button{width:100%;padding:11px;border:0;border-radius:8px;background:#2563eb;color:#fff;
       font-size:14px;cursor:pointer}button:hover{background:#1d4ed8}
#msg{color:#f87171;font-size:12px;min-height:18px;margin-top:10px;text-align:center}
</style></head><body><div class="box">
<h1>QuantDex 控制台</h1><div class="sub">本机私有工具 · 请登录（D-037）</div>
<input id="u" placeholder="账号" autocomplete="username">
<input id="p" type="password" placeholder="密码" autocomplete="current-password">
<button onclick="go()">登 录</button><div id="msg"></div></div>
<script>
async function go(){
  const r=await fetch('/api/login',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({user:u.value,pass:p.value})});
  if(r.ok){location.reload()}else{msg.textContent=(await r.json()).msg||'登录失败'}
}
p.addEventListener('keydown',e=>{if(e.key==='Enter')go()});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json; charset=utf-8",
              cache=None, cookie=None):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        c = self.headers.get("Cookie", "")
        if "qd_session=" not in c:
            return False
        tok = c.split("qd_session=", 1)[1].split(";", 1)[0].strip()
        return _check_token(tok)

    def _audit(self, action: str, detail: str = "") -> None:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"{self.client_address[0]} {action} {detail}" + chr(10))  # newline

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed():
            if u.path.startswith("/api/"):
                return self._send(401, {"authed": False})
            return self._send(200, LOGIN_PAGE.encode("utf-8"),
                              "text/html; charset=utf-8", cache="no-cache")
        try:
            if u.path in ("/", "/index.html"):
                static = _serve_static("index.html")
                if static:
                    # 入口页不缓存：保证发版后刷新即得新版（assets 文件名带哈希可长缓存）
                    return self._send(200, static[0], static[1], cache="no-cache")
                return self._send(503, "前端未构建：请运行 cd frontend && npm run build".encode(),
                                  "text/plain; charset=utf-8")
            if not u.path.startswith("/api/"):
                static = _serve_static(u.path)      # /assets/*.js|css 等前端产物
                if static:
                    return self._send(200, static[0], static[1],
                                       cache="public, max-age=604800")
            if u.path == "/api/overview":
                return self._send(200, overview(q.get("account", ["large"])[0]))
            if u.path == "/api/nav_history":
                return self._send(200, nav_history(q.get("account", ["large"])[0]))
            if u.path == "/api/trades":
                return self._send(200, trades(q.get("account", ["large"])[0],
                                               q.get("limit", ["30"])[0]))
            if u.path == "/api/backtest":
                return self._send(200, backtest(q.get("account", ["large"])[0]))
            if u.path == "/api/rebalance_diff":
                return self._send(200, rebalance_diff(q.get("account", ["large"])[0]))
            if u.path == "/api/fees":
                return self._send(200, fees(q.get("account", ["large"])[0]))
            if u.path == "/api/candidates":
                return self._send(200, candidates(q.get("account", ["large"])[0]))
            if u.path == "/api/school":
                return self._send(200, school_data())
            if u.path == "/api/strategies":
                return self._send(200, {"accounts": [
                    {"key": k, "label": v["label"], "strategy": v["group"]}
                    for k, v in ACCOUNTS.items()]})
            if u.path == "/api/position_alerts":
                return self._send(200, position_alerts(q.get("account", ["large"])[0]))
            if u.path == "/api/positions":
                return self._send(200, {"rows": positions(q.get("account", ["large"])[0])})
            if u.path == "/api/orders":
                return self._send(200, {"rows": orders(q.get("account", ["large"])[0])})
            if u.path == "/api/lookup":
                return self._send(200, symbol_lookup(q.get("symbol", [""])[0]))
            if u.path == "/api/regime":
                return self._send(200, regime())
            if u.path == "/api/reports":
                return self._send(200, {"dates": reports(q.get("account", ["large"])[0])})
            if u.path == "/api/report":
                return self._send(200, {"text": report_text(q.get("account", ["large"])[0],
                                                           q.get("date", [""])[0])})
            if u.path == "/api/run/status":
                return self._send(200, job_status())
            if u.path == "/api/freshness":
                return self._send(200, freshness_info())
            if u.path == "/api/ops":
                return self._send(200, {"rows": ops(q.get("account", ["large"])[0])})
            return self._send(404, {"err": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"err": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        # 防 CSRF（D-034）：表单/simple request 的 Content-Type 不是 application/json；
        # 跨站 JSON 请求需预检（本服务不回 CORS 头即被拦）。Origin 若存在必须同源。
        ctype = self.headers.get("Content-Type", "")
        origin = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not ctype.split(";")[0].strip() == "application/json":
            return self._send(403, {"err": "forbidden: Content-Type 必须为 application/json"})
        if origin:
            from urllib.parse import urlparse as _up
            if _up(origin).netloc not in ALLOWED_ORIGINS:   # R3-18：精确比对防前缀绕过
                return self._send(403, {"err": "forbidden: 跨源请求被拒绝"})
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if u.path == "/api/login":
            if _verify_login(str(body.get("user", "")), str(body.get("pass", ""))):
                self._audit("login", "ok")
                ck = (f"qd_session={_make_token()}; HttpOnly; SameSite=Strict; "
                      f"Max-Age={SESSION_TTL}; Path=/")
                return self._send(200, {"ok": True}, cookie=ck)
            self._audit("login", "FAIL")
            return self._send(401, {"ok": False, "msg": "账号或密码错误"})
        if u.path == "/api/logout":
            ck = "qd_session=; HttpOnly; Max-Age=0; Path=/"
            return self._send(200, {"ok": True}, cookie=ck)
        if not self._authed():
            return self._send(401, {"authed": False})
        try:
            if u.path == "/api/orders/cancel":
                r = cancel_order(body.get("account", "large"), body.get("id"))
                if r.get("ok"):
                    self._audit("cancel", f"{body.get('account')} #{body.get('id')}")
                return self._send(200, r)
            if u.path == "/api/orders/add":
                r = add_order(body.get("account", "large"),
                              body.get("symbol", ""), body.get("side", "buy"),
                              body.get("qty", 0), body.get("price"))
                if r.get("ok"):
                    self._audit("add_order",
                                f"{body.get('account')} {body.get('symbol')} "
                                f"{body.get('side')} {body.get('qty')}")
                return self._send(200, r)
            if u.path == "/api/run":
                self._audit("run_job", body.get("job", "pipeline"))
                return self._send(200, start_job(body.get("job", "pipeline")))
            if u.path == "/api/update":
                self._audit("run_job", "spot_update")
                return self._send(200, start_spot_update())
            return self._send(404, {"err": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"err": str(e)})

    def log_message(self, *args):  # 静默
        pass


class Server(ThreadingHTTPServer):
    """浏览器关页/刷新/取消请求会 RST 连接，属正常现象——不打印整段 traceback。"""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main():
    # 单实例：Windows 的 SO_REUSEADDR 允许"重复绑定"同一端口——旧实例还活着时，
    # 新实例会静默绑定成功但永远收不到连接，于是"改了代码没生效 / 重启了还在报错"。
    # 用跨进程文件锁挡住，并把话说清楚（锁随进程退出由 OS 释放，无陈旧锁）。
    from src.live.proclock import try_lock
    global _instance_lock
    _instance_lock = try_lock(STORE_DIR / "logs" / "webapp.lock")
    if _instance_lock is None:
        print("[启动失败] 已经有一个控制台在跑（webapp.lock 被占用），本实例不启动。")
        print("  关掉那个窗口即可；若窗口已关、进程还在：")
        print("    PowerShell: Get-Process python | Stop-Process -Force")
        raise SystemExit(3)
    try:
        srv = Server((HOST, PORT), Handler)
    except OSError as e:
        print(f"[启动失败] 端口 {PORT} 绑定不上：{e}")
        print("  多半被别的程序占着，换端口或先结束占用它的进程。")
        raise SystemExit(3) from e
    _rotate_log()                                  # 运行日志按大小轮转，防无限长
    threading.Thread(target=auto_update_on_start, daemon=True).start()
    print(f"QuantDex 控制台: http://{HOST}:{PORT}  （也可用 http://localhost:{PORT}）")
    print("（Ctrl+C 退出）启动后自动检查：行情过期→先快照、失败回退 baostock；"
          "当日日报缺失→补跑流水线并发邮件")
    print(f"（每次启动记一行：{START_LOG} ；重复启动 60 分钟内不会重复跑更新）")
    srv.serve_forever()


if __name__ == "__main__":
    main()
