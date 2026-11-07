"""数据单位口径回归测试（D-059）：

- 东财日/分钟线"成交量"（手）必须 ×100 入库（仓库约定 volume=股）；
- em_volume_in_lots 行级检测器要能识别"手"行且对已修复行返回 False（幂等）；
- baostock 分钟线标准化（D-060）：time 列解析、缺列置 NA、source 标记。
"""
import pandas as pd

from src.data.schema import (
    em_volume_in_lots,
    standardize_ak_daily,
    standardize_ak_min,
    standardize_bs_min,
)


def _ak_daily_df():
    """模拟东财 stock_zh_a_hist 返回（中文列、成交量为手）。"""
    return pd.DataFrame({
        "日期": ["2026-11-05", "2026-11-06"],
        "股票代码": ["600519", "600519"],
        "开盘": [10.0, 10.5],
        "收盘": [10.5, 11.0],
        "最高": [10.8, 11.2],
        "最低": [9.9, 10.4],
        "成交量": [12345, 22222],          # 手
        "成交额": [12500000.0, 24000000.0],  # 元
        "振幅": [9.0, 7.6],
        "涨跌幅": [5.0, 4.76],
        "涨跌额": [0.5, 0.5],
        "换手率": [0.5, 0.9],
    })


def test_ak_daily_volume_lots_to_shares():
    out = standardize_ak_daily(_ak_daily_df(), "600519")
    assert out["volume"].tolist() == [1234500.0, 2222200.0]  # ×100
    assert (out["source"] == "akshare/em").all()
    # 换算后隐含 VWAP 必须落回当日价格区间（单位自洽的硬标准）
    vwap = out["amount"] / out["volume"]
    assert (vwap >= out["low"]).all() and (vwap <= out["high"]).all()


def test_ak_min_volume_lots_to_shares():
    df = pd.DataFrame({
        "时间": ["2026-11-06 09:35:00", "2026-11-06 09:40:00"],
        "开盘": [10.0, 10.2], "收盘": [10.2, 10.1],
        "最高": [10.3, 10.3], "最低": [9.9, 10.0],
        "成交量": [500, 300],                 # 手
        "成交额": [510000.0, 305000.0],
    })
    out = standardize_ak_min(df, "600519")
    assert out["volume"].tolist() == [50000.0, 30000.0]
    assert (out["source"] == "akshare/em").all()


def test_em_volume_in_lots_detector_idempotent():
    """检测器识别"手"行；×100 后同一批行不再被命中（修复脚本可安全重跑）。"""
    df = standardize_ak_daily(_ak_daily_df(), "600519").assign(volume=lambda d: d["volume"] / 100)
    # 退化成"手"口径 → 全部命中
    assert em_volume_in_lots(df).all()
    fixed = df.copy()
    fixed.loc[em_volume_in_lots(fixed), "volume"] *= 100.0
    # 已修复 → 一行都不再命中（幂等）
    assert not em_volume_in_lots(fixed).any()
    # 正确数据本身也绝不命中（不会误伤正常行）
    assert not em_volume_in_lots(standardize_ak_daily(_ak_daily_df(), "600519")).any()


def test_bs_min_standardize():
    """baostock 分钟线：time(YYYYMMDDHHMMSSsss) 解析、缺列 NA、source 标记。"""
    raw = pd.DataFrame({
        "date": ["2026-11-05", "2026-11-05"],
        "time": ["20261105093500000", "20261105150000000"],
        "code": ["sh.600519", "sh.600519"],
        "open": ["1446.70", "1450.02"],
        "high": ["1465.80", "1453.00"],
        "low": ["1446.70", "1448.98"],
        "close": ["1465.78", "1453.00"],
        "volume": ["506183", "367000"],   # baostock 分钟线单位=股（已实测验证）
        "amount": ["737628777.0", "533059569.0"],
    })
    out = standardize_bs_min(raw, "600519")
    assert len(out) == 2
    assert str(out["datetime"].iloc[0]) == "2026-11-05 09:35:00"
    assert str(out["datetime"].iloc[1]) == "2026-11-05 15:00:00"
    assert out["volume"].tolist() == [506183.0, 367000.0]  # 不做 ×100
    assert (out["source"] == "baostock/min").all()
    for c in ("turnover", "pct_chg", "change", "amplitude"):  # bs 分钟线无此字段
        assert out[c].isna().all()
    # 数值列已转 numeric
    assert pd.api.types.is_numeric_dtype(out["close"])
