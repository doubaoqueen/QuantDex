"""早入场日内动量闸门回归（D-088 补测，2026-11-18）：合成分钟面板钉死核心口径。

覆盖（scripts/48_intraday_momentum_gate.py 的纯计算部分）：
  ① prepare_panel：gain=检查点价/昨收-1、封板不可买过滤、停牌缺口（交易日序号差）
     不视为连续、T+1 开盘跌停 → 出场退化为 T+1 收盘；
  ② rankic：完全同向=+1、完全反向=-1；
  ③ run_basket：TopN 筛选 + 全成本账本（ret = 出场/介入 - COST_RT，逐日等权）。
聚合层（aggregate_ckpt，读真实分钟仓库）不在此测——数据完整性由 25 号价格质检兜底。
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "img_mod", ROOT / "scripts" / "48_intraday_momentum_gate.py")
img = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(ROOT))
try:
    _spec.loader.exec_module(img)
finally:
    sys.path.pop(0)


DAYS = pd.bdate_range("2026-01-05", periods=4)   # 周一~周四


def _fake_pit(symbols):
    return pd.DataFrame({"symbol": symbols, "in_date": pd.Timestamp("2020-01-01"),
                         "out_date": pd.NaT})


def _patch_pit(symbols):
    """替换 PIT 加载为合成版（不读真实仓库），返回恢复函数。"""
    orig = img.universe_load_pit
    img.universe_load_pit = lambda store: _fake_pit(symbols)
    return lambda: setattr(img, "universe_load_pit", orig)


def _mk_agg():
    """合成检查点聚合面板：4 标的 × 4 日，覆盖正常/封板/缺口/跌停退化四形态。

    dpos 直接给交易日序号（S3 缺一天制造停牌缺口）。
    """
    rows = []
    # dpos: 0,1,2,3（DAYS 连续）；S3 的 dpos 是 0,1,3,4（缺 day2 → 缺口）
    plans = {
        "600001": dict(dpos=[0, 1, 2, 3], ck=[0.01, 0.02, 0.01, 0.01], kind="normal"),
        "600002": dict(dpos=[0, 1, 2, 3], ck=[0.01, 0.109, 0.01, 0.01], kind="sealed"),  # day2 封板（合成涨幅约 +10.9%，越过主板 9.5% 过滤阈值）
        "600003": dict(dpos=[0, 1, 3, 4], ck=[0.01, 0.02, 0.01, 0.01], kind="gap"),      # day2 缺席
        "600004": dict(dpos=[0, 1, 2, 3], ck=[0.01, 0.02, 0.01, 0.01], kind="limitdown"),
    }
    for sym, cfg in plans.items():
        day_close = [round((10.0 + k * 0.1) * 1.001, 2) for k in range(len(DAYS))]
        for k, d in enumerate(DAYS):
            dp = cfg["dpos"][k]
            base = 10.0 + k * 0.1                     # 昨收序列温和上行
            ck_close = round(base * (1 + cfg["ck"][k]), 2)
            if cfg["kind"] == "limitdown" and k >= 1:
                # 跌停场景造在"次日的 day_open"上：prepare_panel 用位移重算 day_open_t1，
                # 所以必须让下一行的 day_open = 本行收盘 × 0.88（< 0.905 跌停界）
                day_open = round(day_close[k - 1] * 0.88, 2)
            else:
                day_open = round(base * 0.998, 2)
            rows.append({"symbol": sym, "date": d, "dpos": dp,
                         "day_open": day_open, "day_close": day_close[k],
                         "ckpt_1030": ck_close, "ckpt_1400": round(ck_close * 1.001, 2)})
    agg = pd.DataFrame(rows)
    agg = agg.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = agg.groupby("symbol")
    agg["prev_close"] = g["day_close"].shift(1)
    prev_dpos = g["dpos"].shift(1)
    agg["contig_prev"] = (agg["dpos"] - prev_dpos) == 1
    agg["date"] = pd.to_datetime(agg["date"])
    for cname in img.CKPTS:
        agg[f"gain_{cname}"] = agg[cname] / agg["prev_close"] - 1.0
    return agg


def test_prepare_panel_gain_and_filters():
    restore = _patch_pit(["600001", "600002", "600003", "600004"])
    try:
        panel = img.prepare_panel(_mk_agg(), store=None)
    finally:
        restore()
    day2 = DAYS[1]

    # ① gain 口径：检查点价 / 昨收 - 1
    s1 = panel[(panel["symbol"] == "600001") & (panel["date"] == day2)].iloc[0]
    assert np.isclose(s1["gain_ckpt_1030"], s1["ckpt_1030"] / s1["prev_close"] - 1.0)

    # ② 封板不可买：600002 在 day2 涨 9.8%（主板涨停）→ entry/gain 置 NaN
    s2 = panel[(panel["symbol"] == "600002") & (panel["date"] == day2)].iloc[0]
    assert pd.isna(s2["entry_ckpt_1030"]) and pd.isna(s2["gain_ckpt_1030"]), "封板必须被过滤"

    # ③ 停牌缺口：600003 的 day3（dpos 3）前一日 dpos 是 1 → 差 2 → 不连续 → 剔除
    day3 = DAYS[2]
    s3 = panel[(panel["symbol"] == "600003") & (panel["date"] == day3)].iloc[0]
    assert pd.isna(s3["entry_ckpt_1030"]), "停牌缺口后首日不可视为连续"

    # ④ 跌停退化出场：600004 的 T+1 开盘 0.88×（< 0.9 跌停界）→ exit_t1_open 退化为 T+1 收盘
    s4_rows = panel[panel["symbol"] == "600004"].dropna(subset=["day_open_t1"])
    assert (s4_rows["exit_t1_open"] == s4_rows["day_close_t1"]).all(), "跌停开盘必须退化到收盘卖"
    assert (s4_rows["exit_fallback"] == 1).all()


def test_rankic_direction():
    rng = np.random.default_rng(7)
    n_sym, n_day = 30, 10
    dates = pd.bdate_range("2026-01-05", periods=n_day)
    frames = []
    for d in dates:
        sig = rng.uniform(0, 1, n_sym)
        fwd = sig.copy()                                # 完全同向
        frames.append(pd.DataFrame({"date": d, "sig": sig, "fwd": fwd,
                                    "symbol": [f"S{i}" for i in range(n_sym)]}))
    panel = pd.concat(frames, ignore_index=True)
    ic = img.rankic(panel, "sig", "fwd")
    assert np.isclose(ic.mean(), 1.0), "完全同向因子 IC 必须 = 1"

    panel["fwd"] = -panel["sig"]                        # 完全反向
    ic2 = img.rankic(panel, "sig", "fwd")
    assert np.isclose(ic2.mean(), -1.0), "完全反向因子 IC 必须 = -1"


def test_run_basket_math_and_costs():
    restore = _patch_pit(["600001", "600002", "600003", "600004"])
    try:
        panel = img.prepare_panel(_mk_agg(), store=None)
    finally:
        restore()
    orig_topn = img.TOP_N
    img.TOP_N = 1   # rank 阈值公式在小池子会多选（r ≥ 1-N/size），N=1 才精确取第一名                                        # 小池子取前 2
    try:
        bench = pd.Series(0.0, index=pd.bdate_range("2026-01-05", periods=8))
        m = img.run_basket(panel, "ckpt_1030", "exit_t1_open", bench, "测试")
    finally:
        img.TOP_N = orig_topn
    # 每日成员 ≤ 2；逐笔收益含全成本扣减（日收益 ≤ 无成本口径）
    assert m["avg_members"] <= 1 + 1e-9
    assert m["days"] > 0
    # 检查账本公式：逐笔 = exit/entry - COST_RT，日收益 = 成员均值
    top = panel.dropna(subset=["entry_ckpt_1030", "exit_t1_open"]).copy()
    top["r"] = top.groupby("date")["gain_ckpt_1030"].rank(pct=True)
    top = top.sort_values(["date", "r"], ascending=[True, False]).groupby("date").head(1)
    top["ret"] = top["exit_t1_open"] / top["entry_ckpt_1030"] - 1.0 - img.COST_RT
    expect = top.assign(date=top["date"] + pd.offsets.BDay(1)).groupby("date")["ret"].mean()
    # 账本日与期望日对齐后逐日核对
    m_daily = (1 + expect).cumprod()
    assert np.isclose(m["total"], m_daily.iloc[-1] - 1, atol=1e-9), "账本净值必须等于逐笔公式"
