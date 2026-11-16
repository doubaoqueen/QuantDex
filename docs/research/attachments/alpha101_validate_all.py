# -*- coding: utf-8 -*-
"""修复验证 + ALPHA101 全量重跑（2026-11-14）。"""
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, 'D:/MeineArbeit/QuantDex')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from src.data.market import get_kline
from src.data.storage import ParquetStore
from src.data.universe import load_pit_constituents
from src.analysis.expr import evaluate_wide
from src.analysis.expr_factors import ALPHA101

STORE = ParquetStore()
START_IC, LABEL_H = '2018-01-01', 5

# ---- 修复验证：5 个此前失败的表达式 ----
idx = pd.date_range('2026-01-01', periods=10)
rng = np.random.default_rng(5)
mk = lambda a, b: pd.DataFrame(rng.normal(a, b, (10, 3)), index=idx, columns=list('ABC'))
fields_sm = {'close': mk(10, 1), 'vwap': mk(10, 1), 'adv30': mk(100, 10),
             'low': mk(9.8, 1), 'high': mk(10.2, 1), 'open': mk(10, 1),
             'volume': pd.DataFrame(np.abs(rng.normal(1000, 100, (10, 3))), index=idx, columns=list('ABC')),
             'amount': pd.DataFrame(np.abs(rng.normal(100000, 10000, (10, 3))), index=idx, columns=list('ABC'))}
fields_sm['adv30'] = fields_sm['amount'].rolling(2).mean()
print('修复验证：')
for name in ('A101_71', 'A101_73', 'A101_77', 'A101_78', 'A101_81'):
    try:
        r = evaluate_wide(ALPHA101[name]['expr'], fields_sm)
        print(f'  {name}: OK {type(r).__name__}')
    except Exception as e:  # noqa: BLE001
        print(f'  {name}: FAIL {type(e).__name__}: {str(e)[:80]}')

# ---- 全量重跑（949 只 PIT 面板） ----
t0 = time.time()
pit = load_pit_constituents("000300", STORE)
symbols = sorted(pit['symbol'].unique().tolist())
loaded = []
for s in symbols:
    try:
        loaded.append((s, get_kline(s, store=STORE)))
    except (FileNotFoundError, OSError):
        pass
symbols = [s for s, _ in loaded]

fields = {}
for f in ('open', 'high', 'low', 'close', 'volume', 'amount', 'turnover'):
    fields[f] = pd.DataFrame({s: df.set_index('date')[f].astype(float) for s, df in loaded})
for k in fields:
    fields[k].index = pd.to_datetime(fields[k].index)
idx = pd.DatetimeIndex(sorted(set().union(*[set(f.index) for f in fields.values()])))
for k in fields:
    fields[k] = fields[k].reindex(idx).sort_index()

C = fields['close']
fields['returns'] = C / C.shift(1) - 1
fields['vwap'] = fields['amount'] / fields['volume'].replace(0, np.nan)
for n in (10, 15, 20, 30, 40, 50, 60, 120, 180):
    fields[f'adv{n}'] = fields['amount'].rolling(n).mean()
dates = fields['close'].index[fields['close'].index >= pd.Timestamp(START_IC)]

member = pd.DataFrame(False, index=idx, columns=symbols)
for r in pit.itertuples():
    if r.symbol not in member.columns:
        continue
    o = pd.Timestamp(r.in_date)
    e = pd.Timestamp(r.out_date) if pd.notna(r.out_date) else pd.Timestamp.max
    mask_dates = idx[(idx >= o) & (idx < e)]
    if len(mask_dates):
        member.loc[mask_dates, r.symbol] = True

def rank_ic(a, l, member):
    valid = member & a.notna() & l.notna()
    av = a.where(valid).rank(axis=1, pct=True)
    lv = l.where(valid).rank(axis=1, pct=True)
    cnt = valid.sum(axis=1).astype(float)
    av_c = av.sub(av.mean(axis=1), axis=0).fillna(0.0) * valid
    lv_c = lv.sub(lv.mean(axis=1), axis=0).fillna(0.0) * valid
    cov = (av_c * lv_c).sum(axis=1)
    std = (av_c.pow(2).sum(axis=1) * lv_c.pow(2).sum(axis=1)) ** 0.5
    ic = cov / std.where(std > 0)
    return ic[cnt >= 30].dropna()

def nw_t(ic):
    n = len(ic)
    if n < 100:
        return np.nan, np.nan
    rho = ic.autocorr(1) or 0.0
    t = ic.mean() / ic.std() * np.sqrt(n)
    return t * np.sqrt((1 - rho) / (1 + rho)), ic.mean()

label = fields['open'].shift(-LABEL_H) / fields['open'].shift(-1) - 1

rows, errors = [], {}
for name, spec in sorted(ALPHA101.items()):
    try:
        a = evaluate_wide(spec['expr'], fields)
        if not isinstance(a, pd.DataFrame):
            errors[name] = '非 DataFrame 返回'
            continue
        ic = rank_ic(a, label, member)
        t_nw, mean = nw_t(ic)
        rows.append({'alpha': name, 'IC均值': round(mean, 4), 'NW-t': round(t_nw, 2), '样本日': len(ic)})
    except Exception as e:  # noqa: BLE001
        errors[name] = f'{type(e).__name__}: {str(e)[:100]}'

print(f'\n全量重跑：求值 {len(rows)} | 失败 {len(errors)} | 耗时 {time.time() - t0:.0f}s')
for k, v in errors.items():
    print('  [错误]', k, v)
res = pd.DataFrame(rows).sort_values('NW-t', key=abs, ascending=False)
print()
print(res.head(15).to_string(index=False))
res.to_csv('D:/MeineArbeit/QuantDex/data/store/research_results/alpha101_ic_full.csv', index=False)
print('已更新 data/store/research_results/alpha101_ic_full.csv')
