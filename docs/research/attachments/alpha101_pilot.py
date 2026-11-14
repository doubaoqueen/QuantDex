# -*- coding: utf-8 -*-
# Alpha101 试点（2026-11-14）：10 个代表表达式 + 3 基准因子，沪深300 PIT 面板 RankIC + 增量检验。
# 结论（详见当日会话/博客待续）：Alpha#40 真增量（残差 t 4.68），Alpha#42 大部分被低波族解释。
# 运行：QuantDex conda 环境 python alpha101_pilot.py（纯本地数据，约 30s）。
# -*- coding: utf-8 -*-
"""Alpha101 试点：10 个代表性表达式 + 3 个基准因子，在沪深300 PIT 面板上跑 RankIC（临时脚本）。

口径对齐 QuantDex 04 号流水线：PIT 成分过滤、2018 起、标签 = 5 日开盘前瞻收益（D-034）。
判定基准（项目纪律）：|IC|>0.03 有戏；试点跨 10 个因子存在多重检验，|t|>3 才值得谈全量移植。
"""
import sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, r'D:\MeineArbeit\QuantDex')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from src.data.market import get_kline
from src.data.storage import ParquetStore
from src.data.universe import load_pit_constituents

STORE = ParquetStore()
START_DATA = '2017-06-01'      # 留 30+ 日 burn-in
START_IC = '2018-01-01'
LABEL_H = 5                    # 5 日前瞻

# ---------------- 数据装载（宽面板：index=date, columns=symbol） ----------------
t0 = time.time()
pit = load_pit_constituents("000300", STORE)
symbols = sorted(pit['symbol'].unique().tolist())
print(f'PIT 并集 {len(symbols)} 只，装载日线...')

fields = {}
missing = []
loaded = []
for s in symbols:
    try:
        df = get_kline(s, store=STORE)
        loaded.append((s, df))
    except (FileNotFoundError, OSError):
        missing.append(s)
print(f'有本地数据 {len(loaded)} 只，缺数据跳过 {len(missing)} 只（历史退市股）')
for f in ('open', 'high', 'low', 'close', 'volume', 'amount', 'turnover'):
    cols = {}
    for s, df in loaded:
        ser = df.set_index('date')[f].astype(float)
        cols[s] = ser[ser.index >= START_DATA]
    fields[f] = pd.DataFrame(cols)
symbols = [s for s, _ in loaded]                 # 掩码列与面板列对齐
print(f'  {f}: {fields[f].shape}')

for k in fields:
    fields[k].index = pd.to_datetime(fields[k].index)
# 对齐日期轴（停牌日天然 NaN）
idx = pd.DatetimeIndex(sorted(set().union(*[set(f.index) for f in fields.values()])))
for k in fields:
    fields[k] = fields[k].reindex(idx).sort_index()
dates = fields['close'].index[fields['close'].index >= pd.Timestamp(START_IC)]

O, H, L, C = fields['open'], fields['high'], fields['low'], fields['close']
V, A, T = fields['volume'], fields['amount'], fields['turnover']
returns = C / C.shift(1) - 1
vwap = A / V.replace(0, np.nan)
adv20 = A.rolling(20).mean()

# ---------------- PIT 成员掩码（in_date <= t < out_date） ----------------
member = pd.DataFrame(False, index=idx, columns=symbols)
for r in pit.itertuples():
    if r.symbol not in member.columns:
        continue
    o = pd.Timestamp(r.in_date)
    e = pd.Timestamp(r.out_date) if pd.notna(r.out_date) else pd.Timestamp.max
    mask_dates = idx[(idx >= o) & (idx < e)]
    if len(mask_dates):
        member.loc[mask_dates, r.symbol] = True
print(f'成员掩码完成，平均每日成员数 {member.sum(axis=1).mean():.0f}')

# ---------------- 算子（宽面板版） ----------------
def cs_rank(df):            # 横截面 rank（0~1）
    return df.rank(axis=1, pct=True)

def ts_rank(df, d):         # 时序 rank：当前值在过去 d 天中的分位
    return df.rolling(d).apply(lambda a: (a <= a[-1]).mean(), raw=True)

def decay_linear(df, d):    # 线性衰减加权均值，权重 d..1 归一化
    w = np.arange(1, d + 1, dtype=float)
    return df.rolling(d).apply(lambda a: float(np.dot(a, w) / w.sum()), raw=True)

def corr(x, y, d):
    return x.rolling(d).corr(y)

# ---------------- Alpha101 试点 10 个 ----------------
def safe_div(a, b):
    return a / b.replace(0, np.nan) if isinstance(b, pd.DataFrame) else a / b

alphas = {}
alphas['Alpha#4']  = -1 * ts_rank(cs_rank(L), 9)
alphas['Alpha#6']  = -1 * corr(O, V, 10)
# Alpha#9：条件反转（ts_min/ts_max 分支）
dm1 = C - C.shift(1)
alphas['Alpha#9']  = pd.DataFrame(
    np.where(dm1.rolling(5).min() > 0, dm1,
             np.where(dm1.rolling(5).max() < 0, dm1, -dm1)),
    index=dm1.index, columns=dm1.columns)
alphas['Alpha#12'] = np.sign(V - V.shift(1)) * (-1 * dm1)
alphas['Alpha#25'] = -1 * cs_rank(returns * adv20 * vwap * (H - C))
alphas['Alpha#40'] = (-1 * cs_rank(H.rolling(10).std())) * corr(H, V, 10)
alphas['Alpha#42'] = cs_rank(vwap - C) / (cs_rank(vwap + C).replace(0, np.nan))
alphas['Alpha#53'] = -1 * (((C - L) - (H - C)) / (C - L + 1e-9)).diff(9)
alphas['Alpha#54'] = -1 * ((L - C) * O ** 5) / ((L - H) * C ** 5 + 1e-9)
alphas['Alpha#101'] = (C - O) / (H - L + 0.001)

# ---------------- 基准因子（项目内现役，同口径对照） ----------------
alphas['基准 turnover_20'] = T.rolling(20).mean()          # 方向已知：负（低换手）
alphas['基准 vol_20'] = C / C.shift(1).sub(1).pipe(lambda s: s)  # 占位（下行重算）
alphas['基准 vol_20'] = returns.rolling(20).std()          # 低波（方向已知：负）
alphas['基准 rev_5'] = C / C.shift(5) - 1                  # 反转（方向已知：负）

# ---------------- RankIC 检验（5 日开盘前瞻标签，D-034 口径） ----------------
label = O.shift(-LABEL_H) / O.shift(-1) - 1

def rank_ic(a, l, member):
    valid = member & a.notna() & l.notna()
    ic = {}
    for d in a.index:
        av, lv, vv = a.loc[d], l.loc[d], valid.loc[d]
        av, lv = av[vv], lv[vv]
        if len(av) < 30:
            continue
        ic[d] = av.rank().corr(lv.rank())
    return pd.Series(ic).dropna()

def nw_t(ic):
    n = len(ic)
    if n < 100:
        return np.nan, np.nan
    rho = ic.autocorr(1) or 0.0
    t = ic.mean() / ic.std() * np.sqrt(n)
    return t * np.sqrt((1 - rho) / (1 + rho)), ic.mean()

print(f'\nIC 窗口：{dates.min().date()} ~ {dates.max().date()}（{len(dates)} 个交易日）')
rows = []
for name, a in alphas.items():
    ic = rank_ic(a, label, member)
    t, mean = nw_t(ic)
    rows.append({'alpha': name, 'IC均值': round(mean, 4), 'IC|均值|': round(abs(mean), 4),
                 'NW-t': round(t, 2), '样本日': len(ic)})
res = pd.DataFrame(rows).sort_values('IC|均值|', ascending=False)
print('\n', res.to_string(index=False))
print('\n判读基准：|IC|>0.03 有戏；试点跨 10 因子多重检验，|NW-t|>3 才值得谈全量移植（D-042 纪律）。')

# ---------------- 增量检验：对现有三因子正交化后的残差 IC ----------------
print('增量检验（横截面回归剔除 基准三因子 后的残差 IC）：')
bench = [alphas['基准 turnover_20'], alphas['基准 vol_20'], alphas['基准 rev_5']]
for name in ('Alpha#40', 'Alpha#42', 'Alpha#12', 'Alpha#6'):
    a = alphas[name]
    resid_ic = {}
    for d in dates:
        av, lv, vv = a.loc[d], label.loc[d], member.loc[d]
        b1, b2, b3 = bench[0].loc[d], bench[1].loc[d], bench[2].loc[d]
        ok = vv & av.notna() & b1.notna() & b2.notna() & b3.notna() & lv.notna()
        if ok.sum() < 30:
            continue
        X = np.column_stack([np.ones(int(ok.sum())),
                             b1[ok].to_numpy(), b2[ok].to_numpy(), b3[ok].to_numpy()])
        y = av[ok].to_numpy()
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = pd.Series(y - X @ beta, index=av[ok].index)
        resid_ic[d] = resid.rank().corr(lv[ok].rank())
    s = pd.Series(resid_ic).dropna()
    t_nw, mean = nw_t(s)
    print(f'  {name}: 残差 IC均值 {mean:.4f} | NW-t {t_nw:.2f} | 样本 {len(s)}')

print(f'总耗时 {time.time() - t0:.0f}s')
