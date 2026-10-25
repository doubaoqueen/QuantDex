# -*- coding: utf-8 -*-
"""
QuantDex 量化学院（面向新人的动态教学页，D-036）。

`python scripts/24_quant_school.py` → http://127.0.0.1:8766

与普通教程的区别 = **动态**：页面里的因子 IC 榜、数据覆盖、账户状态、策略进度
都从本项目 data/store 实时读取——学到的每个概念都能在旁边看到"我们项目里
它长什么样、算出来是多少"。

内容地图：世界观 → 数据 → 因子 → 算子 → 检验 → 策略图谱 → 回测 → 执行 → 纪律。
"""
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

import _bootstrap  # noqa: F401
from config import STORE_DIR
from src.analysis import expr_factors as EF
from src.analysis.factors import FACTOR_NAMES
from src.data.storage import ParquetStore

STORE = ParquetStore()

# ---------------------------------------------------------------- 策略图谱
# 每个条目：思想 / 代表 / 所需因子 / 所需数据 / 我们的状态
STRATEGY_MAP = [
    {"name": "低波动异象", "who": "Ang et al. 2006",
     "idea": "低波动股票长期跑赢高波动（波动错定价）",
     "factors": "vol_20 / STD 系列", "data": "日线",
     "status": "done", "statusText": "已上线（现行版核心）"},
    {"name": "低换手 / 流动性溢价", "who": "Amihud 2002",
     "idea": "换手越冷、流动性越差的股票有溢价",
     "factors": "turnover_20 / AMIHUD", "data": "日线+换手率",
     "status": "done", "statusText": "已上线（现行版核心）"},
    {"name": "股息率 / 红利", "who": "Siegel；中证红利",
     "idea": "高股息=现金流确定+估值保护，收租而非博弈",
     "factors": "dy_ttm", "data": "分红除权记录",
     "status": "done", "statusText": "已上线（div 账户）"},
    {"name": "TopkDropout 换手上界", "who": "qlib",
     "idea": "每期最多换出 n_drop 只，容忍排名抖动降换手",
     "factors": "同现行", "data": "无新增",
     "status": "done", "statusText": "已上线（topk 账户）"},
    {"name": "时间序列动量 / CTA 轮动", "who": "Dennis 海龟；Moskowitz 2012",
     "idea": "资产自身趋势：涨的继续持有，跌的退现金——指数/ETF 轮动",
     "factors": "动量 + 波动率目标", "data": "指数日线（已有）",
     "status": "todo", "statusText": "S2 待做"},
    {"name": "价值（HML）", "who": "Fama-French 1993；Graham",
     "idea": "便宜的股票长期跑赢贵的（PE/PB 低者胜）",
     "factors": "EP = 1/PE、BP = 1/PB", "data": "估值（P1-a：baostock 顺路字段）",
     "status": "data", "statusText": "等 P1-a 补数"},
    {"name": "规模（SMB）", "who": "Fama-French 1993",
     "idea": "小市值溢价（A 股曾显著，注册制后衰减）",
     "factors": "log 市值", "data": "总市值（P1-a）",
     "status": "data", "statusText": "等 P1-a 补数"},
    {"name": "质量 / QMJ", "who": "Novy-Marx 2013；Asness 2019",
     "idea": "高盈利质量（毛利率/ROE）+低杠杆的公司跑赢",
     "factors": "毛利率、ROE、应计", "data": "财务六件套（P1-b）",
     "status": "data", "statusText": "等 P1-b 补数"},
    {"name": "盈利公告漂移 PEAD", "who": "Ball & Brown 1968",
     "idea": "业绩超预期后股价沿方向漂移数周",
     "factors": "SUE（标准化盈余惊喜）", "data": "业绩快报/预告披露日（P1-b）",
     "status": "data", "statusText": "等 P1-b + 披露日"},
    {"name": "偏度 / 彩票偏好", "who": "Bali 2011；Boy 2009",
     "idea": "散户追“彩票股”（高偏度/MAX），其后续跑输",
     "factors": "AL2_MAX20 / AL2_SKEW20", "data": "日线",
     "status": "screen", "statusText": "批次2 已筛选"},
    {"name": "双动量", "who": "Antonacci 2014",
     "idea": "横截面动量 + 时间序列动量的组合，牛市选强、熊市避险",
     "factors": "动量族", "data": "指数/ETF 日线",
     "status": "todo", "statusText": "候选"},
    {"name": "波动率目标 / 风险平价", "who": "AQR；Dalio",
     "idea": "按目标波动率缩放总仓位 / 按风险分配权重",
     "factors": "组合层", "data": "各策略净值",
     "status": "todo", "statusText": "S3 待做"},
    {"name": "横截面动量", "who": "Jegadeesh & Titman 1993",
     "idea": "强者恒强（3-12 月），A 股月频上长期偏弱",
     "factors": "mom_60 / AL2_MOM121", "data": "日线",
     "status": "weak", "statusText": "A 股证据弱（我们 IC 为负）"},
    {"name": "机器学习合成打分", "who": "Gu-Kelly-Xiu 2020；qlib",
     "idea": "ML 学因子非线性组合替代手工等权",
     "factors": "全部因子做特征", "data": "因子矩阵",
     "status": "next", "statusText": "下一阶段（LightGBM）"},
    {"name": "统计套利 / 配对", "who": "Gatev 1999",
     "idea": "协整对价差回归（需做空+高频）",
     "factors": "—", "data": "分钟级+融券",
     "status": "no", "statusText": "A 股 T+1 无融券，不建议"},
    {"name": "事件驱动（回购/解禁）", "who": "实务",
     "idea": "公告事件的方向性漂移",
     "factors": "事件窗口收益", "data": "公告明细（远期）",
     "status": "no", "statusText": "远期"},
]
STATUS_STYLE = {
    "done": ("#059669", "白"), "next": ("#2563eb", "白"), "screen": ("#7c3aed", "白"),
    "data": ("#d97706", "白"), "todo": ("#64748b", "白"), "weak": ("#b45309", "白"),
    "no": ("#9ca3af", "黑"),
}

# ---------------------------------------------------------------- 算子表
OPS_TABLE = [
    ("Ref(x, n)", "n 天前（n>0 过去；n<0 未来——只许标签用）", "$close / Ref($close,5) - 1"),
    ("Mean/Sum/Std(x,n)", "滚动均值/和/标准差", "Std($close/Ref($close,1)-1, 20)"),
    ("Max/Min(x,n)", "滚动最大/最小", "$close/Max($high,120)-1"),
    ("Delta(x,n)", "x - Ref(x,n)", "Delta($close, 20)"),
    ("Corr(x,y,n)", "滚动相关", "Corr($close/Ref($close,1)-1, $turnover, 20)"),
    ("Skew/Kurt(x,n)", "滚动偏度/峰度", "Skew($close/Ref($close,1)-1, 20)"),
    ("Greater/Less(a,b)", "逐元素取大/小", "($high-Greater($open,$close))/$open"),
    ("Abs/Log(x)", "绝对值/对数", "Mean(Abs($close/Ref($close,1)-1)/$amount, 20)"),
    ("+ - * /", "四则运算（除零自动转 NaN）", "($close-$open)/$open"),
]

# ---------------------------------------------------------------- 动态数据
PITFALLS = [
    ("单位陷阱", "北向=亿元、两融=万元、akshare 量=手、新浪换手=小数——新数据源先定量纲再入库"),
    ("复权口径", "qfq 的历史价以今天为锚；策略只用收益率/排名则免疫，用价格水平必须换口径"),
    ("幸存者偏差", "用今天的成分股回测过去=知道谁活下来了——必须 PIT 历史成分（我们因此推翻过最强因子）"),
    ("前视三查", "因子只用过去数据？执行在信号之后？参数不是用全样本挑的？——三问都过才算干净"),
    ("成本现实", "最低佣金 5 元/笔对小资金是 0.8% 起步；印花税只在卖出；涨跌停买不进卖不出"),
    ("防御性减仓三连败", "入场过滤/个股止损/组合断路器都被回测否决（-13~-51pp）——防御在仓位层不在交易层"),
    ("口径一致性", "回测收盘价、实盘开盘价差过 12pp——任何规则两侧（回测/实盘）必须同口径"),
    ("数据不进版本库", "数据是产物不是源码——git 只放架构，数据打包备份（我们 327MB→1.6MB 的教训）"),
]


def _read_json_stats():
    stats = {}
    try:
        reg = pd.read_parquet(STORE_DIR / "registry.parquet")
        g = reg.groupby("dataset").agg(n=("name", "count"))
        stats["daily_syms"] = int(g.loc["daily", "n"]) if "daily" in g.index else 0
        rows = int(reg["rows"].sum())
        stats["total_rows"] = f"{rows / 1e4:.0f} 万"
        stats["datasets"] = int(len(reg))
    except Exception:  # noqa: BLE001
        stats = {"daily_syms": 0, "total_rows": "-", "datasets": 0}
    # 账户
    accounts = []
    for key, name, cap in [("large", "大账户", 1_000_000), ("small", "小账户", 2_000),
                           ("div", "红利账户", 1_000_000), ("topk", "换手账户", 1_000_000)]:
        p = STORE_DIR / "sim" / ({"large": "ledger.sqlite", "small": "ledger_2000.sqlite",
                                  "div": "ledger_div.sqlite", "topk": "ledger_topk.sqlite"}[key])
        if not p.exists():
            accounts.append({"name": name, "nav": None}); continue
        conn = sqlite3.connect(p)
        try:
            df = pd.read_sql_query("SELECT total_nav FROM account_snapshot ORDER BY date DESC LIMIT 1", conn)
        finally:
            conn.close()
        accounts.append({"name": name, "nav": float(df["total_nav"].iloc[0]) if len(df) else None})
    stats["accounts"] = accounts
    # 实验数
    exp_dir = STORE_DIR / "experiments"
    stats["experiments"] = len(list(exp_dir.glob("*.json"))) if exp_dir.exists() else 0
    # 决策数
    dpath = Path("docs/DECISIONS.md")
    stats["decisions"] = len([l for l in dpath.read_text(encoding="utf-8").splitlines()
                              if l.startswith("### D-")]) if dpath.exists() else 0
    return stats


def _ic_table():
    p = STORE_DIR / "factor_results" / "expr_ic_summary.parquet"
    if not p.exists():
        return "<tr><td colspan=6>尚未运行因子筛选（scripts/22）</td></tr>"
    df = pd.read_parquet(p)
    rows = []
    for _, r in df.head(18).iterrows():
        sig = abs(r["t20"]) > 2
        star = " ★" if sig else ""
        rows.append(f"<tr><td>{r['factor']}</td><td>{r['family']}</td>"
                    f"<td>{r['ic20']:+.4f}</td>"
                    f"<td class='{'sig' if sig else ''}'>{r['t20']:+.2f}{star}</td>"
                    f"<td>{r['coverage']:.0%}</td>"
                    f"<td class='mut'>{EF.EXPR_FACTORS.get(r['factor'], {}).get('desc', '')}</td></tr>")
    return "\n".join(rows)


def _factor_count():
    return len(FACTOR_NAMES) + len(EF.EXPR_FACTORS) + 1  # + dy_ttm


def render() -> str:
    st = _read_json_stats()
    acc_cards = "\n".join(
        f"<div class='acc'><span class='mut'>{a['name']}</span>"
        + (f"<b>{a['nav']:,.0f}</b>" if a["nav"] else "<b class='mut'>建仓中</b>")
        + "</div>" for a in st["accounts"])
    strat_rows = "\n".join(
        f"<tr><td><b>{s['name']}</b><span class='mut'><br>{s['who']}</span></td>"
        f"<td>{s['idea']}</td><td class='mono'>{s['factors']}</td>"
        f"<td>{s['data']}</td>"
        f"<td><span class='chip' style='background:{STATUS_STYLE[s['status']][0]}'>"
        f"{s['statusText']}</span></td></tr>"
        for s in STRATEGY_MAP)
    ops_rows = "\n".join(f"<tr><td class='mono'><b>{o}</b></td><td>{d}</td>"
                         f"<td class='mono'>{e}</td></tr>" for o, d, e in OPS_TABLE)
    pit_rows = "\n".join(f"<tr><td><b>{t}</b></td><td>{d}</td></tr>" for t, d in PITFALLS)
    return HTML.format(
        ic_rows=_ic_table(), strat_rows=strat_rows, ops_rows=ops_rows,
        pit_rows=pit_rows, acc_cards=acc_cards,
        n_factors=_factor_count(), n_expr=len(EF.EXPR_FACTORS),
        daily_syms=st["daily_syms"], total_rows=st["total_rows"],
        datasets=st["datasets"], experiments=st["experiments"],
        decisions=st["decisions"])


HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>QuantDex 量化学院</title><style>
*{{box-sizing:border-box}} body{{margin:0;font-family:"Microsoft YaHei",system-ui;background:#0f172a;color:#e2e8f0;line-height:1.75}}
nav{{position:sticky;top:0;z-index:9;background:#0f172acc;backdrop-filter:blur(8px);padding:10px 18px;border-bottom:1px solid #1e293b}}
nav a{{color:#94a3b8;text-decoration:none;margin-right:14px;font-size:13px}}
nav a:hover{{color:#38bdf8}} nav b{{color:#f8fafc;margin-right:16px}}
main{{max-width:1080px;margin:0 auto;padding:28px 20px 80px}}
h1{{font-size:26px;margin:0 0 4px}} .sub{{color:#64748b;font-size:13px;margin-bottom:28px}}
h2{{font-size:19px;margin:44px 0 12px;padding-left:10px;border-left:4px solid #38bdf8;color:#f1f5f9}}
h3{{font-size:15px;color:#cbd5e1}}
p,li{{color:#cbd5e1;font-size:14px}} code,.mono{{font-family:Consolas,monospace;background:#1e293b;color:#7dd3fc;padding:1px 6px;border-radius:5px;font-size:12.5px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}}
.card{{flex:1;min-width:150px;background:#1e293b;border:1px solid #334155;border-radius:12px;padding:14px}}
.card .k{{color:#64748b;font-size:12px}} .card .v{{font-size:22px;font-weight:700;color:#f8fafc}}
.card .v small{{font-size:12px;color:#64748b;font-weight:400}}
.acc{{flex:1;min-width:120px;background:#1e293b;border-radius:12px;padding:10px 14px;display:flex;flex-direction:column;gap:2px}}
.acc b{{font-size:16px;color:#f8fafc}} .acc .mut{{color:#64748b;font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#1e293b;border-radius:10px;overflow:hidden}}
th{{background:#334155;color:#cbd5e1;text-align:left;padding:8px 10px;font-weight:600}}
td{{padding:8px 10px;border-top:1px solid #334155;color:#cbd5e1;vertical-align:top}}
td.sig{{color:#f87171;font-weight:700}} .mut{{color:#64748b;font-size:12px}}
.chip{{padding:2px 10px;border-radius:99px;font-size:11.5px;color:#fff;white-space:nowrap}}
.loop{{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:14px 0}}
.loop span{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:8px 14px;font-size:13px}}
.loop i{{color:#38bdf8;font-style:normal}}
pre{{background:#0b1220;border:1px solid #334155;border-radius:10px;padding:12px 14px;color:#7dd3fc;font-size:12.5px;overflow:auto}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} @media(max-width:800px){{.grid2{{grid-template-columns:1fr}}}}
.note{{background:#172554;border:1px solid #1e40af;border-radius:10px;padding:10px 14px;color:#bfdbfe;font-size:13px;margin:12px 0}}
.warn{{background:#431407;border:1px solid #9a3412;border-radius:10px;padding:10px 14px;color:#fed7aa;font-size:13px;margin:12px 0}}
footer{{text-align:center;color:#475569;font-size:12px;padding:30px}}
</style></head><body>
<nav><b>QuantDex 量化学院</b>
<a href="#world">世界观</a><a href="#data">数据</a><a href="#factor">因子</a>
<a href="#ops">算子</a><a href="#test">检验</a><a href="#strategy">策略图谱</a>
<a href="#backtest">回测</a><a href="#exec">执行</a><a href="#discipline">纪律</a>
<a href="http://127.0.0.1:8765" style="float:right;color:#38bdf8">← 返回控制台</a></nav>
<main>
<h1>量化投资，到底需要什么</h1>
<div class="sub">以 QuantDex 真实项目为教材——页面上每个数字都来自旁边正在运行的项目（{daily_syms} 只标的 / {total_rows} 行 / {datasets} 个数据集 / {n_factors} 个因子 / {experiments} 份实验存档 / {decisions} 条决策记录）</div>

<div class="cards">{acc_cards}</div>

<h2 id="world">一、世界观：一条流水线</h2>
<div class="loop"><span>📡 数据</span><i>→</i><span>🧮 因子</span><i>→</i><span>🎯 策略</span><i>→</i><span>🧪 回测</span><i>→</i><span>🧾 执行</span><i>→</i><span>📊 复盘</span><i>↺</i></div>
<p>量化不是预测水晶球，是<b>把投资决策变成可检验的流水线</b>：数据回答"世界长什么样"，因子把数据压成"分数"，策略把分数变成"买卖规则"，回测验证规则"历史上行不行"，执行把规则跑起来，复盘回答"哪里错了"。任何一环偷懒，后面全白费。本项目四条流水线（四账户）正在这条循环上真实运行。</p>
<div class="note">qlib 第 1 课的世界观：整条流水线可以被"一坨配置"描述——可序列化 = 可复现 = 可改进。这是我们做实验存档的原因。</div>

<h2 id="data">二、数据：量化的大米</h2>
<table><tr><th>层</th><th>内容</th><th>我们项目</th></tr>
<tr><td><b>行情</b></td><td>开高低收/量/额/换手（一切的地基）</td><td class="mut">{daily_syms} 只 × 2015→今，按日分区</td></tr>
<tr><td><b>参考</b></td><td>日历、成分（<b>PIT 历史成分防幸存者偏差</b>）、行业</td><td class="mut">沪深300/中证500 PIT + 申万一级行业</td></tr>
<tr><td><b>公司行为</b></td><td>分红除权（价格连续性）、涨跌停、停牌</td><td class="mut">835 只分红记录 + qfq 复权</td></tr>
<tr><td><b>财务</b></td><td>利润/资产/现金流（基本面因子原料）</td><td class="mut">六件套仅示例——<b>P1-b 待补</b></td></tr>
<tr><td><b>估值</b></td><td>PE/PB/市值（价值/规模因子原料）</td><td class="mut">baostock 顺路字段——<b>P1-a 待补</b></td></tr>
<tr><td><b>资金</b></td><td>北向/两融/龙虎榜（情绪）</td><td class="mut">北向+两融全历史</td></tr>
<tr><td><b>宏观</b></td><td>CPI/利率（择时语境）</td><td class="mut">6 表</td></tr></table>
<div class="warn"><b>新人第一课：单位陷阱。</b>北向=亿元、两融=万元、有的源成交量是"手"有的是"股"、换手率有 0.25 也有 25——不同源混用之前先定量纲，我们为这个修过不下三次。</div>

<h2 id="factor">三、因子：把数据压成分数</h2>
<p>因子 = 每天给每只股票算的一个<b>分数</b>（如"过去 20 天换手率的排名"）。它不是预测，是<b>可检验的猜想</b>：分数高的股票未来是否真的更好？检验手段见第五节。方向不预设——IC 的符号会告诉你"越大越好"还是"越小越好"。</p>
<h3>本项目 {n_factors} 个因子（9 手写 + {n_expr} 表达式 + 股息率），实时 IC 榜（20 日前瞻，★ = |NW-t|>2 显著）</h3>
<table><tr><th>因子</th><th>家族</th><th>IC</th><th>NW-t</th><th>覆盖</th><th>含义</th></tr>{ic_rows}</table>
<div class="warn"><b>看 IC 榜的纪律：</b>这份榜是全样本筛的——从榜上挑因子再做回测 = 用同一份数据又选又考（反转因子因此高估过）。挑进策略前必须过 walk-forward。</div>

<h2 id="ops">四、算子：因子的语法</h2>
<p>因子不用手写 pandas 循环——用<b>表达式</b>（字符串公式）描述，引擎自动算。这是 qlib 教给我们的最重要形态：加一个因子从"改代码"变成"加一行字符串"。</p>
<table><tr><th>算子</th><th>含义</th><th>例句</th></tr>{ops_rows}</table>
<pre>例：Amihud 非流动性（一个真实因子）
  Mean(Abs($close / Ref($close, 1) - 1) / $amount, 20)
  意思：过去 20 天"每天 |涨跌幅| ÷ 成交额"的平均——推动价格 1% 需要多少钱</pre>

<h2 id="test">五、检验：猜想必须过三关</h2>
<div class="grid2">
<div><h3>① RankIC / NW-t</h3><p>每天算"因子排名 vs 未来收益排名"的相关性。|IC 均值|>0.03 有戏，>0.05 不错；持有期>1 天时 IC 自相关，普通 t 值虚高，以 <b>Newey-West t（|t|>2）为准</b>。</p></div>
<div><h3>② ICIR / 分层</h3><p>ICIR = IC 均值/标准差（稳定性）；按分数分 5 组看收益是否<b>单调</b>，多空组差是策略利润的雏形。算术净值会被肥尾欺骗——以中位数口径为准。</p></div>
<div><h3>③ walk-forward</h3><p>每年只用<b>之前</b>的数据选参数，在下一年考试——参数不许偷看未来。它回答"这不是运气吗"（我们当前 OOS：现行 +32.3%、红利 +55.3%、换手 +76.2%）。</p></div>
<div><h3>④ 实盘对照</h3><p>影子盘是终极检验：真实记账、T+1 开盘撮合、费用全扣、人可否决。回测 vs 影子盘的偏差=你所有疏漏的总和（每月归因一次）。</p></div>
</div>

<h2 id="strategy">六、策略图谱：量化界的主流打法 × 我们的状态</h2>
<table><tr><th>策略</th><th>思想</th><th>核心因子</th><th>需要的数据</th><th>我们的进度</th></tr>{strat_rows}</table>
<div class="note">读法：<b>"强大"的前提是适配</b>——统计套利在 A 股 T+1 无融券下无从谈起；横截面动量在 A 股月频长期偏弱；而低波/低换手/红利这些"冷门"家族在 A 股是真金白银。先看数据有什么，再看策略要什么，最后看约束允许什么。</div>

<h2 id="backtest">七、回测：诚实的时光机</h2>
<p>回测最大的敌人不是算错，是<b>悄悄作弊</b>。四条保真铁律（每条都来自我们真实踩坑）：</p>
<table><tr><th>铁律</th><th>我们踩的坑</th></tr>
<tr><td>T+1：信号次日才能成交（且<b>口径必须统一</b>）</td><td>回测收盘价 vs 实盘开盘价差过 12pp（D-034）</td></tr>
<tr><td>成本：佣金+最低佣金+印花税+过户+滑点</td><td>最低佣金对小资金=0.8% 起步</td></tr>
<tr><td>不可成交：涨停买不进/跌停卖不出/停牌跳过</td><td>分板块 10%/20%，容差 0.1%</td></tr>
<tr><td>PIT：只用当时真实存在的成分与披露</td><td>幸存者偏差曾把我们最强因子从 +0.042 改写成 -0.003</td></tr></table>

<h2 id="exec">八、执行：从纸面到账户</h2>
<p>策略再好，执行层会把它打回原形。我们的形态：<b>每日收盘后流水线生成挂单 → 人工在控制台审批（可否决）→ 次日开盘按价撮合 → 涨跌停/停牌自动拒单 → 每晚一封合并日报</b>。三个账户用不同策略并行，谁好谁坏让净值说话。</p>
<div class="warn"><b>防御性减仓三连败</b>（我们用回测否决了三次直觉）：弱市减仓、个股止损、组合断路器全部是负贡献。防御的正确位置是"投入多少钱"（风险预算），不是"跌了怎么办"（交易规则）。</div>

<h2 id="discipline">九、纪律：比策略更值钱的部分</h2>
<table><tr><th>机制</th><th>作用</th><th>我们的实现</th></tr>
<tr><td>决策日志</td><td>记录"当时为什么这么定"，防事后自欺</td><td class="mut">{decisions} 条（D-001~）</td></tr>
<tr><td>实验存档</td><td>参数↔结果↔代码版本绑定，三个月后可追溯</td><td class="mut">{experiments} 份 JSON</td></tr>
<tr><td>知识库</td><td>每个坑写成"现象→根因→修复→教训"</td><td class="mut">quantdex_vault（本地）</td></tr>
<tr><td>风险预算</td><td>最大可承受亏损 ÷ 策略历史回撤 = 最大投入</td><td class="mut">实盘前的最后一道门</td></tr></table>
<p style="margin-top:18px">—— 学完这一页，你需要的"必需之物"就是：干净的数据、可检验的猜想、诚实的时光机、能跑的账户、和不骗自己的纪律。</p>
</main>
<footer>QuantDex 量化学院 · 数据实时取自本地 data/store · 配套控制台 http://127.0.0.1:8765</footer>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, render().encode("utf-8"))
        return self._send(404, "not found".encode())

    def log_message(self, *a):  # noqa
        pass


def main():
    port = 8766
    print(f"QuantDex 量化学院: http://127.0.0.1:{port}  （Ctrl+C 退出）")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
