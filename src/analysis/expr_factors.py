# -*- coding: utf-8 -*-
"""表达式因子库（AlphaLite32）：对标 qlib Alpha158 的"产能形态"，字段用我们
的真实数据（close/open/high/low/volume/amount/turnover——特别是真实换手率，
qlib 样例库没有）。所有表达式只用 Ref(x, n>0)（只看过去），无前视。

维护铁律（用户决策 2026-10-24）：**新增因子一律写表达式进本文件**，
不再手写 pandas；现有 9 个手写因子保留不动。
"""
from __future__ import annotations

EXPR_FACTORS: dict[str, dict] = {
    # ---------------- K 线形态（一根 K 线的 6 个视角，qlib KMID 家族）
    "KMID":    {"expr": "($close - $open) / $open",
                "desc": "K线实体占比（阳线幅度）", "family": "K线"},
    "KLEN":    {"expr": "($high - $low) / $open",
                "desc": "K线全长振幅", "family": "K线"},
    "KUP":     {"expr": "($high - Greater($open, $close)) / $open",
                "desc": "上影线占比", "family": "K线"},
    "KLOW":    {"expr": "(Less($open, $close) - $low) / $open",
                "desc": "下影线占比", "family": "K线"},
    "KSFT":    {"expr": "(2 * $close - $high - $low) / $open",
                "desc": "收盘在K线内的偏移", "family": "K线"},
    "KPOS":    {"expr": "($close - $low) / ($high - $low + 1e-9)",
                "desc": "收盘价在日内区间的位置(0~1)", "family": "K线"},
    # ---------------- 动量 / 反转（多窗口 ROC）
    "ROC5":    {"expr": "$close / Ref($close, 5) - 1",
                "desc": "5日收益率（短期反转）", "family": "动量"},
    "ROC10":   {"expr": "$close / Ref($close, 10) - 1",
                "desc": "10日收益率", "family": "动量"},
    "ROC20":   {"expr": "$close / Ref($close, 20) - 1",
                "desc": "20日收益率（动量）", "family": "动量"},
    "ROC60":   {"expr": "$close / Ref($close, 60) - 1",
                "desc": "60日收益率（中期动量）", "family": "动量"},
    # ---------------- 波动率（多窗口）
    "STD5":    {"expr": "Std($close / Ref($close, 1) - 1, 5)",
                "desc": "5日收益波动", "family": "波动"},
    "STD20":   {"expr": "Std($close / Ref($close, 1) - 1, 20)",
                "desc": "20日收益波动（=vol_20 的表达式版）", "family": "波动"},
    "STD60":   {"expr": "Std($close / Ref($close, 1) - 1, 60)",
                "desc": "60日收益波动", "family": "波动"},
    # ---------------- 均线乖离
    "MA5BIAS":  {"expr": "$close / Mean($close, 5) - 1",
                 "desc": "5日均线乖离", "family": "均线"},
    "MA10BIAS": {"expr": "$close / Mean($close, 10) - 1",
                 "desc": "10日均线乖离", "family": "均线"},
    "MA20BIAS": {"expr": "$close / Mean($close, 20) - 1",
                 "desc": "20日均线乖离", "family": "均线"},
    # ---------------- 位置 / 通道
    "QTLU20":  {"expr": "$close / Max($high, 20) - 1",
                "desc": "距20日最高价的距离", "family": "位置"},
    "QTDL20":  {"expr": "$close / Min($low, 20) - 1",
                "desc": "距20日最低价的距离", "family": "位置"},
    "WIDTH20": {"expr": "(Max($high, 20) - Min($low, 20)) / Mean($close, 20)",
                "desc": "20日通道宽度（波动代理）", "family": "位置"},
    # ---------------- 量能
    "VMA5_60": {"expr": "Mean($volume, 5) / Mean($volume, 60)",
                "desc": "量能趋势（5日均量/60日均量）", "family": "量能"},
    "VROC5":   {"expr": "$volume / Ref($volume, 5) - 1",
                "desc": "5日量变", "family": "量能"},
    "VSTD20":  {"expr": "Std($volume, 20) / Mean($volume, 20)",
                "desc": "成交量波动系数", "family": "量能"},
    "AMT20":   {"expr": "Mean($amount, 20)",
                "desc": "20日均成交额（规模/流动性代理）", "family": "量能"},
    # ---------------- 换手（我们的数据优势：qlib 样例库没有真实换手率）
    "TURN20":  {"expr": "Mean($turnover, 20)",
                "desc": "20日均换手（=turnover_20 的表达式版）", "family": "换手"},
    "TSTD20":  {"expr": "Std($turnover, 20)",
                "desc": "换手率波动（情绪不稳定度）", "family": "换手"},
    "TCV20":   {"expr": "Std($turnover, 20) / Mean($turnover, 20)",
                "desc": "换手率变异系数", "family": "换手"},
    # ---------------- 量价相关
    "CORR_RT20":  {"expr": "Corr($close / Ref($close, 1) - 1, $turnover, 20)",
                   "desc": "量价相关（20日，收益×换手）", "family": "量价"},
    "CORR_AMT20": {"expr": "Corr($close / Ref($close, 1) - 1, $amount, 20)",
                   "desc": "量价相关（20日，收益×成交额）", "family": "量价"},
    # ---------------- 流动性 / 冲击成本
    "AMIHUD20": {"expr": "Mean(Abs($close / Ref($close, 1) - 1) / $amount, 20)",
                 "desc": "Amihud 非流动性（=amihud_20 的表达式版）", "family": "流动性"},
    "ILLIQ5":   {"expr": "Mean(Abs($close / Ref($close, 1) - 1) / $amount, 5)",
                 "desc": "5日 Amihud（短期冲击）", "family": "流动性"},
    # ---------------- 其他结构
    "MGAP20":  {"expr": "Mean($open / Ref($close, 1) - 1, 20)",
                "desc": "20日平均隔夜跳空（情绪外溢）", "family": "结构"},
    "KMID20":  {"expr": "Mean(($close - $open) / $open, 20)",
                "desc": "20日平均实体方向（趋势一致性）", "family": "结构"},
}

EXPR_NAMES = list(EXPR_FACTORS)


def evaluate_all(fields: dict, names: list[str] | None = None) -> dict[str, "object"]:
    """对一个标的的字段字典批量求值，返回 {因子名: Series}。"""
    from src.analysis.expr import evaluate
    out = {}
    for name in (names or EXPR_NAMES):
        out[name] = evaluate(EXPR_FACTORS[name]["expr"], fields)
    return out
