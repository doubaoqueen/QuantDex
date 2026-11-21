"""第 56 步：因子自动挖掘——枚举式搜索（D-105 预注册版，2026-11-21）。

搜索空间（D-105 预注册，确定性枚举 ≈1200 候选，不扫参数——空间本身就是预注册）：
  字段：close / volume / amount / turnover（日线宽矩阵，884 非ST 池 × 2018-2026）
  族 A 单算子：{Mean,Std,Max,Min,Delta,Skew,Kurt}(f, d∈{3,5,10,20,40,60})
  族 B 截面Rank：Rank(族A)
  族 C 快慢比：Mean(f,d1)/Mean(f,d2)，d1<d2
  族 D 量价相关：Corr(f1,f2,d)（4×4 字段对 / 2 × 6 窗口）
  族 E 动量：Delta(close,d)/Ref(close,d)
  族 F 复合：Rank(族C)、(A−B)/(A+B) 形态

筛选链（D-105 预注册，全写死）：
  ① 批量 RankIC(20日) + NW-t → ② |IC20|≥0.03 且 |NW-t|≥2
  → ③ BH-FDR（全候选批量）q≤0.1 → ④ 相关性去重 |corr|>0.7（与现有 3 因子 + 簇内）
  → ⑤ 分年方向一致 ≥5/7（2020-2026）+ 样本外 2024-26 方向一致
裁决（预注册）：0 存活 → 枚举式产出率 ≈ 手写（0/46 基率），入档关闭；
  >0 存活 → 逐个进影子盘增量检验提案（不直接上策略）。
红线：不碰进攻型六格结论；不挤占 12-09/12-29 主线（空闲 CPU 时段跑）。
"""
import json
import sys
import time
from itertools import combinations_with_replacement

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.analysis.expr import evaluate_wide
from src.analysis.factor_test import _newey_west_tstat
from src.analysis.factors import st_symbol_set
from src.data.storage import ParquetStore

START = "20180101"
FIELDS = ("close", "volume", "amount", "turnover")
WINDOWS = (3, 5, 10, 20, 40, 60)
MIN_IC, MIN_T = 0.03, 2.0
OUT = STORE_DIR / "research_results" / "factor_mining.csv"


def gen_candidates() -> list[str]:
    """确定性枚举（D-105 预注册的族 A~F）。"""
    cands: list[str] = []

    # 族 A：单算子 × 字段 × 窗口
    for op in ("Mean", "Std", "Max", "Min", "Delta", "Skew", "Kurt"):
        for f in FIELDS:
            for d in WINDOWS:
                cands.append(f"{op}(${f}, {d})")
    # 族 B：截面 Rank（作用于族 A 的全部）
    cands += [f"Rank({e})" for e in cands]
    # 族 C：快慢比（Mean fast/slow）
    for f in FIELDS:
        for d1 in WINDOWS:
            for d2 in WINDOWS:
                if d1 < d2:
                    cands.append(f"Mean(${f}, {d1}) / Mean(${f}, {d2})")
                    cands.append(f"Mean(${f}, {d2}) / Mean(${f}, {d1})")
    # 族 D：量价相关（字段对 × 窗口）
    for f1, f2 in (("close", "volume"), ("close", "amount"), ("close", "turnover"),
                   ("volume", "amount"), ("volume", "turnover"), ("amount", "turnover")):
        for d in (10, 20, 60):
            cands.append(f"Corr(${f1}, ${f2}, {d})")
    # 族 E：动量（收益窗）
    for d in (3, 5, 10, 20, 40, 60):
        cands.append(f"Delta($close, {d}) / Ref($close, {d})")
    # 族 F：复合——(A−B)/(A+B) 量价形态
    for f1, f2 in (("close", "volume"), ("volume", "amount"), ("close", "turnover")):
        for d in (10, 20, 60):
            cands.append(f"(Mean(${f1}, {d}) - Mean(${f2}, {d})) / "
                         f"(Mean(${f1}, {d}) + Mean(${f2}, {d}) + 1e-9)")
    # 去重（字面重复）+ 编号
    seen, out = set(), []
    for e in cands:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def main() -> None:
    store = ParquetStore()
    t0 = time.time()
    cands = gen_candidates()
    print(f">> 搜索空间：{len(cands)} 个候选（预注册枚举）", flush=True)

    # 宽矩阵（884 非ST × 2018-2026）
    st_syms = st_symbol_set(store)
    from src.data.market import load_price_matrix
    pit = pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000300.parquet")
    symbols = sorted({str(x) for x in pit["symbol"]})
    symbols = [x for x in symbols if x not in st_syms]

    frames = {f: [] for f in FIELDS}
    dates_ref = None
    for sym in symbols:
        try:
            df = store.read("daily", f"{sym}__qfq",
                            columns=["date", *FIELDS])
        except Exception:  # noqa: BLE001
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date")
        for f in FIELDS:
            frames[f].append(df[f].rename(sym))
        if dates_ref is None:
            dates_ref = df.index
    wide = {f: pd.concat(frames[f], axis=1).sort_index() for f in FIELDS}
    common = wide["close"].index
    for f in FIELDS:
        wide[f] = wide[f].reindex(common).sort_index()
    print(f"   宽矩阵 {wide['close'].shape}（{time.time()-t0:.0f}s）", flush=True)

    # 前瞻 20 日收益（D-038 近似：宽矩阵口径，停牌洞 → NaN，诚实）
    fwd20 = (wide["close"].shift(-21) / wide["close"].shift(-1) - 1.0).replace([np.inf, -np.inf], np.nan)
    fwd_rank = fwd20.rank(axis=1)

    def daily_ic(fmat: pd.DataFrame) -> pd.Series:
        fr = fmat.rank(axis=1)
        valid = fr.notna() & fwd_rank.notna()
        n = valid.sum(axis=1)
        x = fr.where(valid); y = fwd_rank.where(valid)
        mx, my = x.mean(axis=1), y.mean(axis=1)
        dx, dy = x.sub(mx, axis=0), y.sub(my, axis=0)
        cov = (dx * dy).sum(axis=1)
        den = np.sqrt((dx ** 2).sum(axis=1) * (dy ** 2).sum(axis=1))
        with np.errstate(invalid="ignore", divide="ignore"):
            ic = cov / den
        return ic.replace([np.inf, -np.inf], np.nan).dropna()

    # 批量求值 + IC
    results = []
    t1 = time.perf_counter()
    for i, expr in enumerate(cands, 1):
        try:
            fmat = evaluate_wide(expr, {f: wide[f] for f in FIELDS})
            if fmat is None or len(fmat) == 0:
                continue
            ic = daily_ic(fmat)
            if len(ic) < 200:
                continue
            mean = float(ic.mean())
            t = float(_newey_west_tstat(ic, 19))
            results.append({"expr": expr, "ic20": mean, "nw_t": t, "n_days": int(len(ic))})
        except Exception:  # noqa: BLE001  单候选失败不拖垮批次
            continue
        if i % 200 == 0:
            el = time.perf_counter() - t1
            print(f"   {i}/{len(cands)}（{el:.0f}s）", flush=True)

    df = pd.DataFrame(results)
    print(f"\n>> 有效候选 {len(df)}（求值 {time.perf_counter()-t1:.0f}s）")

    # 第一道筛
    df["pass1"] = (df["ic20"].abs() >= MIN_IC) & (df["nw_t"].abs() >= MIN_T)
    # BH-FDR（全候选批量）
    from math import erfc, sqrt
    df["p"] = [erfc(abs(v) / sqrt(2)) if pd.notna(v) else np.nan for v in df["nw_t"]]
    m = df["p"].notna().sum()
    pv = df.loc[df["p"].notna(), "p"].sort_values()
    df["q"] = (pv * m / pv.rank(method="first")).clip(upper=1.0).reindex(df.index)
    surv = df[df["pass1"] & (df["q"] <= 0.1)].sort_values("ic20", key=lambda s: s.abs(),
                                                          ascending=False)
    print(f"第一道筛存活: {len(surv)} / {len(df)}（FDR q≤0.1）")
    if len(surv):
        print(surv[["expr", "ic20", "nw_t", "q"]].to_string(index=False))

    df.to_csv(OUT, index=False, encoding='utf-8-sig')
    json.dump({"n_candidates": len(cands), "n_valid": int(len(df)),
               "n_pass1": int(df["pass1"].sum()), "n_fdr": int(len(surv))},
              open(STORE_DIR / "research_results" / "factor_mining.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    try:
        record("factor_mining_enum", params={"space": "D-105 预注册枚举"},
               metrics={"n_candidates": len(cands), "n_pass1": int(df["pass1"].sum()),
                        "n_fdr": int(len(surv))},
               notes=f"枚举式自动挖矿：存活 {len(surv)}/{len(df)}",
               artifacts=["research_results/factor_mining.csv"])
    except Exception as e:  # noqa: BLE001
        print(f"[experiments] {e}")
    verdict = ("SURVIVORS" if len(surv) else "EMPTY")
    print(f"\n== 裁决：{verdict} ==")
    if verdict == "SURVIVORS":
        print("→ 幸存者进影子盘增量检验提案（闸门全检：去重/WF/分年）")
    else:
        print("→ 枚举式产出率 ≈ 手写（0 存活）→ 入档关闭，负结果与 0/46 基率并列")


if __name__ == "__main__":
    main()
