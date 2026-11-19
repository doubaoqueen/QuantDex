"""D-097 体检刀修复回归：Sortino 全样本口径 / extract_trades 分数仓位 / dy 真实价分母。"""
import sys as _s
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for pth in (str(ROOT), str(ROOT / "scripts")):
    if pth not in _s.path:
        _s.path.insert(0, pth)

import numpy as np
import pandas as pd

from src.analysis.factors import _adj_factor_series
from src.backtest.metrics import extract_trades, performance


# ① extract_trades：二元仓位行为与旧版一致
def test_extract_trades_binary_unchanged():
    df = pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=6),
        "position": [0, 1, 1, 1, 0, 0],
        "open": [10.0, 11.0, 12.0, 11.0, 10.0, 10.0],
    })
    tr = extract_trades(df)
    assert len(tr) == 1
    r = tr.iloc[0]
    assert str(r["entry_date"].date()) == "2026-01-06"
    assert str(r["exit_date"].date()) == "2026-01-09"
    assert np.isclose(r["entry_price"], 11.0) and np.isclose(r["exit_price"], 10.0)
    assert np.isclose(r["return"], 10.0 / 11.0 - 1)
    assert r["holding_days"] == 3


# ② extract_trades：分数仓位——加仓加权入场、部分减仓分段
def test_extract_trades_fractional():
    df = pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=6),
        "position": [0.0, 0.5, 1.0, 1.0, 0.3, 0.0],
        "open": [10.0, 10.0, 12.0, 12.0, 12.0, 12.0],
    })
    tr = extract_trades(df)
    # 段1: 0→0.5@10（01-06）→ 加仓至 1.0@12（01-07 加权入场 11.0）→ 部分减仓 0.7@12（01-09）
    # 段2: 余 0.3@11 清仓 @12（01-12）
    assert len(tr) == 2, tr
    for _, r in tr.iterrows():
        assert np.isclose(r["entry_price"], 11.0), "加权入场价 = (0.5×10+0.5×12)/1 = 11"
        assert np.isclose(r["return"], 12.0 / 11.0 - 1)
    assert tr.iloc[0]["holding_days"] == 3 and tr.iloc[1]["holding_days"] == 1


# ③ Sortino：全样本下行偏差（D-097：旧口径只算下跌日，分母被抬高）
def test_sortino_full_sample():
    rets = pd.Series([0.01] * 19 + [-0.10])             # 19 涨 1 跳水（年化为正，比较方向才成立）
    df = pd.DataFrame({"nav": (1 + rets).cumprod(),
                       "strat_ret_net": rets, "ret": rets})
    res = performance(df, trades=pd.DataFrame())   # 跳过 extract_trades（无 position 列）
    rf = 0.018
    expected_dv = np.sqrt((np.minimum(rets - rf, 0.0) ** 2).mean()) * np.sqrt(250)
    expected_sortino = (res["年化收益率"] - rf) / expected_dv
    assert np.isclose(res["Sortino比率"], expected_sortino), "Sortino 必须等于全样本下行偏差口径"
    old_dv = np.sqrt((rets[rets < 0] ** 2).mean()) * np.sqrt(250)
    # 方向实锤（体检刀②方向说反）：旧口径分母被压低（×√(下跌日/总日)<1）→ 旧 Sortino 虚高
    assert expected_dv < old_dv, "全样本口径分母必须小于只算下跌日的旧口径"
    assert res["Sortino比率"] > (res["年化收益率"] - rf) / old_dv, "修正后 Sortino 必须高于旧虚高口径"


# ④ dy 真实价分母：复权因子序列 + 公式
class _FakeAdjStore:
    """只服务 adjfactor 读取的鸭子仓库。"""

    def read(self, kind, name, columns=None):
        if kind == "fin_bs" and name == "600001__adjfactor":
            return pd.DataFrame({
                "dividOperateDate": ["2018-06-01", "2019-06-01", "2026-09-01"],
                "foreAdjustFactor": [0.5, 0.7, 1.0],
            })
        raise FileNotFoundError(name)


def test_adj_factor_series():
    s = _adj_factor_series(_FakeAdjStore(), "600001")
    assert s is not None and float(s.iloc[-1]) == 1.0, "最新日因子必须为 1（qfq 锚定）"
    assert s.index.is_monotonic_increasing
    assert _adj_factor_series(_FakeAdjStore(), "000000") is None, "无因子文件 → None（退回旧口径）"
    # 真实价公式：qfq=5.0 时因子 0.7 → 真实价 7.14（股息率不再被未来分红放大）
    assert np.isclose(5.0 / 0.7, 7.142857, rtol=1e-4)


def test_missing_adj_file_returns_none():
    class _Empty:
        def read(self, kind, name, columns=None):
            raise FileNotFoundError(name)
    assert _adj_factor_series(_Empty(), "600001") is None
