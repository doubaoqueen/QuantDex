"""涨跌停幅度判定回归（深检 2026-11-22）：年代口径 / ST / 板块。

背景：旧实现恒按"创/科 ±20%、主板 ±10%、ST ±5%"，存在三类错误——
① 创业板 2020-08-24 注册制前真实 ±10%（2018 起的回测里涨停买进被误放行）；
② 科创板/注册制后创业板的 ST 仍按所在板幅度（旧实现一律压到 ±5%）；
③ 北交所 ±30% 缺失。
"""
import pandas as pd

from config import board_limit


def test_main_board_and_st():
    assert board_limit("600519") == 0.10
    assert board_limit("000001") == 0.10
    assert board_limit("600519", "ST长油") == 0.05
    assert board_limit("600519", "*ST长油") == 0.05
    assert board_limit("600519", "st长油") == 0.05      # 大小写不敏感
    assert board_limit("600519", "ST 长油") == 0.05     # 含空格（去空格后判定）


def test_gem_anachronism_fixed():
    """创业板注册制（2020-08-24）前后 ±10% / ±20%。"""
    assert board_limit("300750", date="2019-06-03") == 0.10
    assert board_limit("300750", date="2020-08-23") == 0.10
    assert board_limit("300750", date="2020-08-24") == 0.20
    assert board_limit("300750", date=pd.Timestamp("2020-08-24")) == 0.20
    assert board_limit("301111", date="2022-01-01") == 0.20
    # 不传日期 = 实盘快照/展示口径（现行规则）
    assert board_limit("300750") == 0.20


def test_gem_st_by_era():
    """ST 幅度随板块与年代：注册制前创业板 ST ±5%，之后与科创板 ST 均 ±20%。"""
    assert board_limit("300750", "ST某股", date="2019-06-03") == 0.05
    assert board_limit("300750", "ST某股", date="2020-08-24") == 0.20
    assert board_limit("688009", "ST某股", date="2025-01-01") == 0.20


def test_star_and_bj():
    assert board_limit("688009") == 0.20
    assert board_limit("688009", date="2019-07-22") == 0.20
    assert board_limit("830799", date="2022-01-01") == 0.30   # 北交所老号段
    assert board_limit("920002", date="2024-01-01") == 0.30   # 北交所新号段
    assert board_limit("430047", date="2023-01-01") == 0.30


def test_date_formats_accepted():
    """Timestamp / 'YYYY-MM-DD' / 'YYYYMMDD' 三种口径等价。"""
    for d in ("20200824", "2020-08-24", pd.Timestamp("2020-08-24")):
        assert board_limit("300750", date=d) == 0.20
    assert board_limit("300750", date="20190603") == 0.10
    assert board_limit("300750", date=None) == 0.20
