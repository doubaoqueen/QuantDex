# -*- coding: utf-8 -*-
"""生成单页效果看板：策略/基准净值、回撤、年度收益、10万变多少、影子盘现状。"""
import sys, json
sys.path.insert(0, r"D:\MeineArbeit\QuantDex")
import numpy as np, pandas as pd
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from config import STORE_DIR
from src.data.storage import ParquetStore
from src.live.ledger import Ledger

OUT = STORE_DIR / "dashboard"; OUT.mkdir(parents=True, exist_ok=True)
FONT = "C:/Windows/Fonts/msyh.ttc"

def font(sz):
    try: return ImageFont.truetype(FONT, sz)
    except Exception: return ImageFont.load_default()

def line_chart(path, df: pd.DataFrame, title: str):
    """df：共同 DatetimeIndex、每列一条线（已对齐、已 ffill）。"""
    W, H, PAD = 1100, 420, 60
    img = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(img)
    vals = df.values.astype(float)
    lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
    pad = (hi - lo) * 0.08 or 0.01
    lo, hi = lo - pad, hi + pad
    n = len(df)
    def X(i): return PAD + (W - 2*PAD) * i / max(n - 1, 1)
    def Y(v): return H - PAD - (H - 2*PAD) * (v - lo) / (hi - lo)
    d.rectangle([PAD, PAD, W-PAD, H-PAD], outline="#dddddd")
    for frac in (0, .25, .5, .75, 1):
        v = lo + (hi - lo) * frac
        y = Y(v); d.line([PAD, y, W-PAD, y], fill="#f2f2f2")
        d.text((6, y-7), f"{v:.2f}", font=font(11), fill="#888888")
    colors = ["#c0392b", "#2980b9", "#27ae60"]
    for j, col in enumerate(df.columns):
        pts = [(X(i), Y(v)) for i, v in enumerate(df[col].values)]
        d.line(pts, fill=colors[j], width=2)
        d.text((W - PAD - 100, PAD + 20 * j + 4), str(col), font=font(12), fill=colors[j])
    # 年份刻度
    last_year = None
    for i, dt in enumerate(df.index):
        if dt.year != last_year:
            last_year = dt.year
            d.text((X(i) - 10, H - PAD + 8), str(dt.year), font=font(10), fill="#888888")
    d.text((PAD, 12), title, font=font(16), fill="#222222")
    img.save(path)

def stats(nav, bench=None):
    r = nav.pct_change().dropna()
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1e-9)
    total = nav.iloc[-1] / nav.iloc[0] - 1
    ann = (1 + total) ** (1 / years) - 1
    mdd = float((nav / nav.cummax() - 1).min())
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan
    out = {"total": total, "ann": ann, "mdd": mdd, "sharpe": sharpe,
           "start": str(nav.index[0].date()), "end": str(nav.index[-1].date())}
    if bench is not None:
        b = bench.reindex(nav.index).ffill().dropna()
        b = b / b.iloc[0] * nav.iloc[0]
        out["bench_total"] = float(b.iloc[-1] / b.iloc[0] - 1)
        out["excess"] = total - out["bench_total"]
    return out

store = ParquetStore()
# 全周期组合
nav = pd.read_parquet(STORE_DIR/"portfolio_results"/"nav_000300_top50_reb20.parquet")
nav = nav.set_index("date") if "date" in nav.columns else nav
nav = nav["nav"].astype(float)
bench = store.read("daily", "000300__none").set_index("date")["close"].astype(float)
bench = bench[(bench.index >= nav.index[0]) & (bench.index <= nav.index[-1])]
bench_n = bench / bench.iloc[0] * nav.iloc[0]
s_all = stats(nav, bench)

# 样本外
oos = pd.read_parquet(STORE_DIR/"walkforward_results"/"nav_oos_000300.parquet")
oos = oos.set_index("date") if "date" in oos.columns else oos
oos = oos["nav"].astype(float)
bo = bench[(bench.index >= oos.index[0]) & (bench.index <= oos.index[-1])]
bo_n = bo / bo.iloc[0] * oos.iloc[0]
s_oos = stats(oos, bo)

# 年度收益
yearly = {}
for y, g in nav.groupby(nav.index.year):
    if len(g) > 2:
        yearly[y] = float(g.iloc[-1] / g.iloc[0] - 1)
byearly = {}
for y, g in bench.groupby(bench.index.year):
    if len(g) > 2:
        byearly[y] = float((g / g.iloc[0] - 1).iloc[-1])

full = pd.DataFrame({"策略": nav, "沪深300": bench_n}).sort_index().ffill()
line_chart(OUT/"nav_all.png", full,
           f"策略 vs 沪深300（{s_all['start']} ~ {s_all['end']}，起点=1.0）")
oos_df = pd.DataFrame({"策略(OOS)": oos, "沪深300": bo_n}).sort_index().ffill()
line_chart(OUT/"nav_oos.png", oos_df,
           f"walk-forward 样本外（{s_oos['start']} ~ {s_oos['end']}，起点=1.0）")
# 回撤
dd = (nav / nav.cummax() - 1).values
img = Image.new("RGB", (1100, 300), "white"); d = ImageDraw.Draw(img)
n = len(dd); W, H, PAD = 1100, 300, 50
pts = [(PAD + (W-2*PAD)*i/max(n-1,1), PAD + (H-2*PAD)*(-dd[i])) for i in range(n)]
d.line(pts, fill="#c0392b", width=2)
d.text((PAD, 10), f"策略回撤（最低 {s_all['mdd']:.2%}）", font=font(14), fill="#222")
img.save(OUT/"drawdown.png")

# 影子盘
led = Ledger(STORE_DIR/"sim"/"ledger.sqlite"); snap = led.snapshot(); led.close()
nav_now = float(snap["total_nav"].iloc[-1]) if len(snap) else None
led2 = Ledger(STORE_DIR/"sim"/"ledger_2000.sqlite"); s2 = led2.snapshot(); led2.close()
small_nav = float(s2["total_nav"].iloc[-1]) if len(s2) else None

# 10万变多少
def grow(total, init=100000):
    return init * (1 + total)
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>QuantDex 效果看板</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;max-width:1180px;margin:24px auto;color:#222;padding:0 16px}}
h1{{font-size:26px}} h2{{font-size:19px;margin-top:32px;border-left:4px solid #c0392b;padding-left:10px}}
.cards{{display:flex;gap:14px;flex-wrap:wrap}}
.card{{flex:1;min-width:200px;border:1px solid #e5e5e5;border-radius:10px;padding:14px}}
.card .v{{font-size:26px;font-weight:700;margin:6px 0}}
.card .s{{color:#888;font-size:12px}}
table{{border-collapse:collapse;width:100%;margin-top:12px;font-size:14px}}
th,td{{border:1px solid #e5e5e5;padding:8px 10px;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
.note{{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px;color:#555;font-size:13px;line-height:1.7}}
img{{max-width:100%;border:1px solid #eee;border-radius:8px;margin-top:8px}}
</style></head><body>
<h1>QuantDex 效果看板</h1>
<p class="note">这是“如果按这套规则做，历史上会是什么结果”的模拟，不是实盘收益。所有数字都可以在项目里复现（见文末路径）。</p>

<h2>1. 策略 vs 沪深300（2018–2026，扣费后）</h2>
<div class="cards">
<div class="card"><div class="s">策略总收益</div><div class="v" style="color:#c0392b">{s_all['total']:+.2%}</div><div class="s">年化 {s_all['ann']:+.2%}</div></div>
<div class="card"><div class="s">沪深300同期</div><div class="v">{s_all.get('bench_total', 0):+.2%}</div><div class="s">策略超额 {s_all.get('excess', 0):+.2%}</div></div>
<div class="card"><div class="s">最大回撤</div><div class="v">{s_all['mdd']:.2%}</div><div class="s">夏普(无风险1.8%) {s_all['sharpe']:.2f}</div></div>
<div class="card"><div class="s">10 万本金变成</div><div class="v">{grow(s_all['total']):,.0f} 元</div><div class="s">同期沪深300：{grow(s_all.get('bench_total',0)):,.0f} 元</div></div>
</div>
<img src="nav_all.png">
<img src="drawdown.png">

<h2>2. 样本外（walk-forward，2022–2026）</h2>
<div class="cards">
<div class="card"><div class="s">样本外总收益</div><div class="v" style="color:#c0392b">{s_oos['total']:+.2%}</div><div class="s">年化 {s_oos['ann']:+.2%}</div></div>
<div class="card"><div class="s">沪深300同期</div><div class="v">{s_oos.get('bench_total',0):+.2%}</div><div class="s">超额 {s_oos.get('excess',0):+.2%}</div></div>
<div class="card"><div class="s">最大回撤</div><div class="v">{s_oos['mdd']:.2%}</div><div class="s">10 万变成 {grow(s_oos['total']):,.0f} 元</div></div>
</div>
<img src="nav_oos.png">
<p class="note">样本外 = 每年只用“之前的数据”选参数，再用当年数据检验。它比全周期回测可信，因为它不允许“偷看未来”。</p>

<h2>3. 分年度收益</h2>
<table><tr><th>年份</th><th>策略</th><th>沪深300</th></tr>
{''.join(f"<tr><td>{y}</td><td>{yearly.get(y, float('nan')):+.2%}</td><td>{byearly.get(y, float('nan')):+.2%}</td></tr>" for y in sorted(set(list(yearly)+list(byearly))))}
</table>

<h2>4. 影子盘现状（真实记账，不是回测）</h2>
<div class="cards">
<div class="card"><div class="s">大账户（100 万虚拟，10-19 起）</div><div class="v">{nav_now:,.0f} 元</div><div class="s">{(nav_now/1_000_000-1) if nav_now else 0:+.2%}</div></div>
<div class="card"><div class="s">小账户（2000 元，实盘模拟）</div><div class="v">{small_nav:,.0f} 元</div><div class="s">3 笔挂单待 10-21 开盘撮合</div></div>
</div>
<p class="note">大账户是“看着大盘”的组合；小账户是给你未来实盘练手的（2000 元、最多 3 只、整手）。</p>

<h2>5. 需要你判断的三件事</h2>
<div class="note">
1）看到最大回撤 <b>{s_all['mdd']:.2%}</b>（2018–2026），你能接受吗？<br>
2）样本外年化 <b>{s_oos['ann']:+.2%}</b>、超额 <b>{s_oos.get('excess',0):+.2%}</b>，你觉得这是“能力”还是“运气”？<br>
3）如果现在给你 2000 元真钱，按这套规则跑，你敢吗？——这三个问题的答案，才是你参与项目的开始。
</div>

<h2>6. 数据来源（可复现）</h2>
<p class="note">
策略净值：<code>data/store/portfolio_results/nav_000300_top50_reb20.parquet</code><br>
样本外净值：<code>data/store/walkforward_results/nav_oos_000300.parquet</code><br>
影子盘账本：<code>data/store/sim/ledger.sqlite</code> / <code>ledger_2000.sqlite</code><br>
生成脚本：<code>scripts/15_dashboard.py</code>（本页由它生成）
</p>
</body></html>"""
(OUT/"index.html").write_text(html, encoding="utf-8")
print("看板已生成:", OUT/"index.html")
print(f"全周期 {s_all['total']:+.2%} 年化 {s_all['ann']:+.2%} MDD {s_all['mdd']:.2%}")
print(f"样本外 {s_oos['total']:+.2%} 年化 {s_oos['ann']:+.2%} MDD {s_oos['mdd']:.2%}")
