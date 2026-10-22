# -*- coding: utf-8 -*-
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
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

import _bootstrap  # noqa: F401
from config import STORE_DIR
from src.data.market import get_kline
from src.data.storage import ParquetStore

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS = {
    "large": {"db": STORE_DIR / "sim" / "ledger.sqlite", "label": "大账户（100万）"},
    "small": {"db": STORE_DIR / "sim" / "ledger_2000.sqlite", "label": "小账户（2000元）"},
}
LOG = STORE_DIR / "logs" / "webapp_run.log"
_store = ParquetStore()
_cache = {"regime": (0.0, None)}
_job = {"proc": None, "name": None}


def _db(acc):
    p = ACCOUNTS[acc]["db"]
    if not p.exists():
        return None
    return sqlite3.connect(p)


def _tail(df, n=1):
    return df.tail(n)


def overview(acc):
    conn = _db(acc)
    if conn is None:
        return {"account": acc, "empty": True}
    try:
        snap = pd.read_sql_query("SELECT * FROM account_snapshot ORDER BY date", conn)
        pend = pd.read_sql_query("SELECT * FROM trade_log WHERE status='pending'", conn)
        pos = pd.read_sql_query("SELECT * FROM positions WHERE date=(SELECT MAX(date) FROM positions)", conn)
        out = {"account": acc, "label": ACCOUNTS[acc]["label"], "empty": False,
               "date": str(snap["date"].iloc[-1]) if len(snap) else None,
               "nav": float(snap["total_nav"].iloc[-1]) if len(snap) else None,
               "cash": float(snap["cash"].iloc[-1]) if len(snap) else None,
               "mv": float(snap["market_value"].iloc[-1]) if len(snap) else None,
               "prev_nav": float(snap["total_nav"].iloc[-2]) if len(snap) >= 2 else None,
               "positions": int(len(pos)), "pending": int(len(pend))}
        out["day_pnl"] = (out["nav"] - out["prev_nav"]) if out["prev_nav"] else 0.0
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
    rows = []
    for r in pos.itertuples(index=False):
        prev = None
        try:
            k = get_kline(str(r.symbol), adjust="qfq", store=_store)
            k = k[pd.to_datetime(k["date"]) <= pd.Timestamp(last)]
            if len(k) >= 2:
                prev = float(k["close"].iloc[-2])
        except Exception:  # noqa: BLE001
            pass
        px = float(r.close)
        chg = (px / prev - 1) if prev else None
        pnl = (px - prev) * float(r.shares) if prev else 0.0
        rows.append({"symbol": str(r.symbol), "shares": float(r.shares), "close": px,
                     "pct": chg, "pnl": pnl, "weight": float(r.weight)})
    return rows


def orders(acc):
    conn = _db(acc)
    if conn is None:
        return []
    try:
        df = pd.read_sql_query(
            "SELECT id,symbol,side,qty,price,fill_date,status,note FROM trade_log "
            "WHERE status='pending' ORDER BY id", conn)
        return df.to_dict("records")
    finally:
        conn.close()


def cancel_order(acc, order_id):
    conn = _db(acc)
    if conn is None:
        return {"ok": False, "msg": "账户不存在"}
    try:
        cur = conn.execute(
            "UPDATE trade_log SET status='cancelled', note=note || '（人工取消）' "
            "WHERE id=? AND status='pending'", (int(order_id),))
        conn.commit()
        return {"ok": cur.rowcount > 0, "msg": "已取消" if cur.rowcount else "订单不存在或非 pending"}
    finally:
        conn.close()


def add_order(acc, symbol, side, qty, price=None):
    symbol = str(symbol).strip()
    if side not in ("buy", "sell"):
        return {"ok": False, "msg": "方向只能是 buy/sell"}
    qty = int(qty)
    if qty <= 0 or qty % 100 != 0:
        return {"ok": False, "msg": "数量必须是 100 的整数倍"}
    try:
        k = get_kline(symbol, adjust="qfq", store=_store)
    except Exception:  # noqa: BLE001
        return {"ok": False, "msg": f"没有 {symbol} 的行情数据"}
    px = float(price) if price else float(k["close"].iloc[-1])
    import pandas as pd2
    cal = pd2.read_parquet(STORE_DIR / "meta" / "trade_calendar.parquet")
    cal = pd2.to_datetime(cal["date"]).sort_values()
    last = pd2.to_datetime(k["date"]).max()
    nxt = cal[cal > last]
    fill = str(nxt.iloc[0].date()) if len(nxt) else str((last + pd2.Timedelta(days=1)).date())
    conn = _db(acc)
    if conn is None:
        return {"ok": False, "msg": "账户不存在"}
    try:
        amount = px * qty
        fee = max(amount * 0.00025, 5.0) + amount * 0.00001
        conn.execute(
            "INSERT INTO trade_log(signal_date,fill_date,symbol,side,qty,price,amount,"
            "fee,stamp_duty,status,note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(last.date()), fill, symbol, side, qty, px, amount,
             fee, amount * 0.0005 if side == "sell" else 0.0, "pending", "人工下单"))
        conn.commit()
        return {"ok": True, "msg": f"已挂单：{symbol} {side} {qty} 股，成交日 {fill}"}
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
        import os
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
    p = STORE_DIR / "reports" / acc / f"{date}.md"
    return p.read_text(encoding="utf-8") if p.exists() else "未找到日报"


def start_job(name):
    if _job["proc"] is not None and _job["proc"].poll() is None:
        return {"ok": False, "msg": f"已有任务在跑：{_job['name']}"}
    import subprocess
    import sys
    py = sys.executable
    if name == "update":
        cmd = [py, str(ROOT / "scripts" / "02b_update_pit.py")]
    else:
        cmd = [py, str(ROOT / "scripts" / "10_daily_run.py"), "--skip-update", "--no-push"]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOG, "a", encoding="utf-8")
    f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {name} =====\n")
    f.flush()
    _job["proc"] = subprocess.Popen(cmd, cwd=str(ROOT), stdout=f, stderr=f)
    _job["name"] = name
    return {"ok": True, "msg": f"已启动：{name}"}


def job_status():
    p = _job["proc"]
    running = p is not None and p.poll() is None
    tail = ""
    if LOG.exists():
        lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        tail = "\n".join(lines[-25:])
    return {"running": running, "name": _job["name"], "log": tail}


PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>QuantDex 控制台</title><style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#222}
header{background:#1f2937;color:#fff;padding:12px 20px;display:flex;align-items:center;gap:14px}
header h1{font-size:17px;margin:0;flex:1}
button{cursor:pointer;border:0;border-radius:6px;padding:7px 12px;font-size:13px;background:#2563eb;color:#fff}
button.gray{background:#6b7280} button.red{background:#dc2626}
main{max-width:1200px;margin:16px auto;padding:0 14px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.card{flex:1;min-width:220px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px}
.card .s{color:#6b7280;font-size:12px} .card .v{font-size:22px;font-weight:700;margin-top:4px}
section{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px;margin-bottom:14px}
h2{font-size:15px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid #f0f0f0;text-align:right}
th:first-child,td:first-child{text-align:left}
.red{color:#dc2626}.green{color:#059669}
input,select{padding:6px;border:1px solid #d1d5db;border-radius:6px;font-size:13px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#toast{position:fixed;right:16px;bottom:16px;background:#111827;color:#fff;padding:10px 14px;border-radius:8px;display:none}
pre{background:#0b1220;color:#c9d1d9;padding:10px;border-radius:8px;max-height:260px;overflow:auto;font-size:12px}
.badge{padding:2px 8px;border-radius:999px;font-size:12px;color:#fff}
</style></head><body>
<header><h1>QuantDex 控制台</h1>
<button onclick="runJob('update')">更新行情</button>
<button onclick="runJob('pipeline')">跑流水线</button>
<button class="gray" onclick="loadAll()">刷新</button></header>
<main>
<div class="cards">
  <div class="card" id="card-large"></div>
  <div class="card" id="card-small"></div>
  <div class="card" id="card-regime"></div>
</div>

<section><h2>持仓 <span id="pos-title" class="s"></span></h2>
<table><thead><tr><th>代码</th><th>股数</th><th>现价</th><th>涨跌幅</th><th>当日盈亏</th><th>权重</th></tr></thead>
<tbody id="pos-body"></tbody></table></section>

<section><h2>待成交订单（可人工取消）</h2>
<table><thead><tr><th>代码</th><th>方向</th><th>数量</th><th>参考价</th><th>成交日</th><th>操作</th></tr></thead>
<tbody id="ord-body"></tbody></table>
<div class="row" style="margin-top:10px">
  <select id="acc"><option value="large">大账户</option><option value="small">小账户</option></select>
  <input id="sym" placeholder="代码 如 600519" size="10">
  <select id="side"><option value="buy">买入</option><option value="sell">卖出</option></select>
  <input id="qty" placeholder="数量(100倍)" size="10">
  <button onclick="addOrder()">人工挂单</button>
</div></section>

<section><h2>市场状态</h2><div id="regime-detail" class="s"></div></section>

<section><h2>日报</h2>
<div class="row"><select id="rpt-acc" onchange="loadReports()"><option value="large">大账户</option><option value="small">小账户</option></select>
<select id="rpt-date" onchange="loadReport()"></select></div>
<pre id="rpt-text" style="margin-top:10px;max-height:340px"></pre></section>

<section><h2>后台任务日志</h2><pre id="job-log"></pre></section>
</main><div id="toast"></div>
<script>
const fmt=(v,d=2)=>v==null?'-':Number(v).toLocaleString(undefined,{maximumFractionDigits:d});
const pct=v=>v==null?'-':(v*100).toFixed(2)+'%';
const cls=v=>v>0?'red':(v<0?'green':'');
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',2500)}
async function j(u,o){const r=await fetch(u,o);return await r.json()}
function card(id,o){const el=document.getElementById(id);if(!o||o.empty){el.innerHTML='<div class="s">'+id+'：暂无数据</div>';return}
 const pnl=o.day_pnl||0;
 el.innerHTML='<div class="s">'+o.label+(o.date?' · '+o.date:'')+'</div>'+
 '<div class="v">'+fmt(o.nav)+' 元</div>'+
 '<div class="s">现金 '+fmt(o.cash)+' · 持仓 '+o.positions+' · 待成交 '+o.pending+'</div>'+
 '<div class="s '+cls(pnl)+'">今日 '+(pnl>=0?'+':'')+fmt(pnl)+'（'+(o.prev_nav?pct(o.nav/o.prev_nav-1):'-')+'）</div>'}
async function loadAll(){
 const [L,S,R]=await Promise.all([j('/api/overview?account=large'),j('/api/overview?account=small'),j('/api/regime')]);
 card('card-large',L);card('card-small',S);
 const g=document.getElementById('card-regime');
 const light=(R.above60&&R.above200)?'#059669':((R.above200)?'#f59e0b':'#dc2626');
 g.innerHTML='<div class="s">市场状态 · '+R.date+'</div><div class="v"><span class="badge" style="background:'+light+'">'+(R.above60&&R.above200?'偏强':(R.above200?'震荡':'偏弱'))+'</span></div>'+
 '<div class="s">沪深300 '+fmt(R.close)+' · MA20 '+fmt(R.ma20)+' · MA60 '+fmt(R.ma60)+' · MA200 '+fmt(R.ma200)+'</div>'+
 '<div class="s">近20日 '+pct(R.ret20)+' · 近60日 '+pct(R.ret60)+' · 回撤 '+pct(R.dd)+'</div>'+
 '<div class="s">站上MA60 的股票比例 '+(R.breadth==null?'-':pct(R.breadth))+'</div>';
 document.getElementById('regime-detail').innerHTML=
  '站上 MA20：'+(R.above20?'是':'否')+' ｜ 站上 MA60：'+(R.above60?'是':'否')+' ｜ 站上 MA200：'+(R.above200?'是':'否')+
  '<br>近120日 '+pct(R.ret120)+' ｜ 距一年高点 '+pct(R.dd)+' ｜ 宽度 '+pct(R.breadth);
 const acc=document.getElementById('acc').value;
 const P=await j('/api/positions?account=large');
 document.getElementById('pos-title').textContent='（大账户）';
 const tb=document.getElementById('pos-body');tb.innerHTML='';
 (P.rows||[]).sort((a,b)=>b.weight-a.weight).slice(0,30).forEach(r=>{
  tb.insertAdjacentHTML('beforeend','<tr><td>'+r.symbol+'</td><td>'+r.shares+'</td><td>'+fmt(r.close)+'</td>'+
   '<td class="'+cls(r.pct)+'">'+pct(r.pct)+'</td><td class="'+cls(r.pnl)+'">'+fmt(r.pnl,0)+'</td><td>'+pct(r.weight)+'</td></tr>')});
 const O=await j('/api/orders?account=large');
 const ob=document.getElementById('ord-body');ob.innerHTML='';
 (O.rows||[]).forEach(o=>{ob.insertAdjacentHTML('beforeend','<tr><td>'+o.symbol+'</td><td>'+o.side+'</td><td>'+o.qty+'</td><td>'+fmt(o.price)+'</td><td>'+o.fill_date+'</td>'+
  '<td><button class="red" onclick="cancelOrder('+o.id+')">取消</button></td></tr>')});
 loadReports();
}
async function cancelOrder(id){if(!confirm('取消该订单？'))return;const r=await j('/api/orders/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:'large',id})});toast(r.msg);loadAll()}
async function addOrder(){const body={account:document.getElementById('acc').value,symbol:document.getElementById('sym').value,side:document.getElementById('side').value,qty:document.getElementById('qty').value};const r=await j('/api/orders/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});toast(r.msg);loadAll()}
async function runJob(n){const r=await j('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job:n})});toast(r.msg);pollJob()}
async function pollJob(){try{const s=await j('/api/run/status');document.getElementById('job-log').textContent=s.log||'';if(s.running)setTimeout(pollJob,3000);else loadAll()}catch(e){}}
async function loadReports(){const acc=document.getElementById('rpt-acc').value;const R=await j('/api/reports?account='+acc);const sel=document.getElementById('rpt-date');sel.innerHTML='';R.dates.forEach(d=>{const o=document.createElement('option');o.value=d;o.textContent=d;sel.appendChild(o)});if(R.dates.length)loadReport()}
async function loadReport(){const acc=document.getElementById('rpt-acc').value;const d=document.getElementById('rpt-date').value;const r=await j('/api/report?account='+acc+'&date='+d);document.getElementById('rpt-text').textContent=r.text}
loadAll();setInterval(loadAll,15000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/overview":
                return self._send(200, overview(q.get("account", ["large"])[0]))
            if u.path == "/api/positions":
                return self._send(200, {"rows": positions(q.get("account", ["large"])[0])})
            if u.path == "/api/orders":
                return self._send(200, {"rows": orders(q.get("account", ["large"])[0])})
            if u.path == "/api/regime":
                return self._send(200, regime())
            if u.path == "/api/reports":
                return self._send(200, {"dates": reports(q.get("account", ["large"])[0])})
            if u.path == "/api/report":
                return self._send(200, {"text": report_text(q.get("account", ["large"])[0],
                                                           q.get("date", [""])[0])})
            if u.path == "/api/run/status":
                return self._send(200, job_status())
            return self._send(404, {"err": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"err": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        try:
            if u.path == "/api/orders/cancel":
                return self._send(200, cancel_order(body.get("account", "large"), body.get("id")))
            if u.path == "/api/orders/add":
                return self._send(200, add_order(body.get("account", "large"),
                                                 body.get("symbol", ""), body.get("side", "buy"),
                                                 body.get("qty", 0), body.get("price")))
            if u.path == "/api/run":
                return self._send(200, start_job(body.get("job", "pipeline")))
            return self._send(404, {"err": "not found"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"err": str(e)})

    def log_message(self, *args):  # 静默
        pass


def main():
    host, port = "127.0.0.1", 8765
    print(f"QuantDex 控制台: http://{host}:{port}  （Ctrl+C 退出）")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
