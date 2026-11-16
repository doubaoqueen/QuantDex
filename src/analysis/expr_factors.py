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


def evaluate_all(fields: dict, names: list[str] | None = None) -> dict[str, object]:
    """对一个标的的字段字典批量求值，返回 {因子名: Series}。"""
    from src.analysis.expr import evaluate
    out = {}
    for name in (names or EXPR_NAMES):
        out[name] = evaluate(EXPR_FACTORS[name]["expr"], fields)
    return out

# ============================================================
# 批次 2（AL2_*，2026-10-26）：面向"强大策略家族"的补充因子
# 每个因子标注服务的策略家族；Ref 全部为正（无前视）
# ============================================================
EXPR_FACTORS.update({
    "AL2_52W":      {"expr": "$close / Max($high, 120) - 1",
                     "desc": "距半年高点（52周高点锚定效应，George-Hwang）", "family": "动量"},
    "AL2_MOM121":   {"expr": "Ref($close, 20) / Ref($close, 120) - 1",
                     "desc": "跳过近20日的120日动量（12-1动量的日频版）", "family": "动量"},
    "AL2_MAX20":    {"expr": "Max($close / Ref($close, 1) - 1, 20)",
                     "desc": "20日内最大单日涨幅（彩票偏好 MAX 效应，越高越差）", "family": "情绪"},
    "AL2_MIN20":    {"expr": "Min($close / Ref($close, 1) - 1, 20)",
                     "desc": "20日内最差单日（尾部风险）", "family": "风险"},
    "AL2_SKEW20":   {"expr": "Skew($close / Ref($close, 1) - 1, 20)",
                     "desc": "20日收益偏度（偏度溢价：负偏股票溢价）", "family": "风险"},
    "AL2_KURT20":   {"expr": "Kurt($close / Ref($close, 1) - 1, 20)",
                     "desc": "20日收益峰度（肥尾程度）", "family": "风险"},
    "AL2_VS_R":     {"expr": "Std($close / Ref($close, 1) - 1, 5) / (Std($close / Ref($close, 1) - 1, 60) + 1e-9)",
                     "desc": "短长期波动比（波动突变）", "family": "风险"},
    "AL2_SHARPE20": {"expr": "Mean($close / Ref($close, 1) - 1, 20) / (Std($close / Ref($close, 1) - 1, 20) + 1e-9)",
                     "desc": "20日滚动夏普（趋势质量）", "family": "动量"},
    "AL2_ATR20":    {"expr": "Mean(($high - $low) / $close, 20)",
                     "desc": "20日平均日内振幅（ATR 比率版）", "family": "风险"},
    "AL2_GPV20":    {"expr": "Std($open / Ref($close, 1) - 1, 20)",
                     "desc": "隔夜跳空波动（情绪过夜外溢）", "family": "情绪"},
    "AL2_TREND_T":  {"expr": "Mean($turnover, 5) / (Mean($turnover, 60) + 1e-9)",
                     "desc": "换手趋势（关注升温/降温）", "family": "换手"},
    "AL2_CORR60":   {"expr": "Corr($close / Ref($close, 1) - 1, $volume, 60)",
                     "desc": "60日量价相关（长窗确认）", "family": "量价"},
    "AL2_VSPIKE":   {"expr": "$volume / (Mean($volume, 20) + 1e-9)",
                     "desc": "当日量比异常（放量突变）", "family": "量能"},
    "AL2_DONCH20":  {"expr": "($close - Min($low, 20)) / (Max($high, 20) - Min($low, 20) + 1e-9)",
                     "desc": "20日唐奇安区间位置（突破系统核心）", "family": "动量"},
})


# ================= Alpha101（WorldQuant《101 Formulaic Alphas》公开研报移植，2026-11-14） =================
# 闸门候选备粮：全部标记未准入（D-061 边界：公开文献移植 ≠ 挖新因子；准入仍走 D-073 三关）。
# 字段依赖：宽面板求值（evaluate_wide）需调用方提供派生字段 $returns/$vwap/$advN（N 日均成交额）。
# 移植状态：61/101 已按可核验原文转写；84~100 原文被引用长度截断待核对；
#           含 IndNeutralize（58/59/63/67/69/70/76/79/80/82）与 $cap（56）的 11 个待行业数据。
# 分母零保护：+1e-9 为转写补充（原研报未加，除零在 A 股停牌/平盘日真实存在）。
ALPHA101: dict[str, dict] = {
    "A101_01": "Rank(TsArgMax(SignedPower(If($returns < 0, Std($returns, 20), $close), 2), 5)) - 0.5",
    "A101_02": "-1 * Corr(Rank(Delta(Log($volume), 2)), Rank(($close - $open) / $open), 6)",
    "A101_03": "-1 * Corr(Rank($open), Rank($volume), 10)",
    "A101_04": "-1 * TsRank(Rank($low), 9)",
    "A101_05": "Rank($open - Sum($vwap, 10) / 10) * (-1 * Abs(Rank($close - $vwap)))",
    "A101_06": "-1 * Corr($open, $volume, 10)",
    "A101_07": "If($adv20 < $volume, -1 * TsRank(Abs(Delta($close, 7)), 60) * Sign(Delta($close, 7)), -1)",
    "A101_08": "-1 * Rank(Sum($open, 5) * Sum($returns, 5) - Ref(Sum($open, 5) * Sum($returns, 5), 10))",
    "A101_09": "If(Min(Delta($close, 1), 5) > 0, Delta($close, 1), If(Max(Delta($close, 1), 5) < 0, Delta($close, 1), -1 * Delta($close, 1)))",
    "A101_10": "Rank(If(Min(Delta($close, 1), 4) > 0, Delta($close, 1), If(Max(Delta($close, 1), 4) < 0, Delta($close, 1), -1 * Delta($close, 1))))",
    "A101_11": "(Rank(Max($vwap - $close, 3)) + Rank(Min($vwap - $close, 3))) * Rank(Delta($volume, 3))",
    "A101_12": "Sign(Delta($volume, 1)) * (-1 * Delta($close, 1))",
    "A101_13": "-1 * Rank(Cov(Rank($close), Rank($volume), 5))",
    "A101_14": "(-1 * Rank(Delta($returns, 3))) * Corr($open, $volume, 10)",
    "A101_15": "-1 * Sum(Rank(Corr(Rank($high), Rank($volume), 3)), 3)",
    "A101_16": "-1 * Rank(Cov(Rank($high), Rank($volume), 5))",
    "A101_17": "((-1 * Rank(TsRank($close, 10))) * Rank(Delta(Delta($close, 1), 1))) * Rank(TsRank($volume / $adv20, 5))",
    "A101_18": "-1 * Rank(Std(Abs($close - $open), 5) + ($close - $open) + Corr($close, $open, 10))",
    "A101_19": "(-1 * Sign(($close - Ref($close, 7)) + Delta($close, 7))) * (1 + Rank(1 + Sum($returns, 250)))",
    "A101_20": "(-1 * Rank($open - Ref($high, 1))) * Rank($open - Ref($close, 1)) * Rank($open - Ref($low, 1))",
    "A101_22": "-1 * (Delta(Corr($high, $volume, 5), 5) * Rank(Std($close, 20)))",
    "A101_23": "If(Sum($high, 20) / 20 < $high, -1 * Delta($high, 2), 0)",
    "A101_25": "Rank((-1 * $returns) * $adv20 * $vwap * ($high - $close))",
    "A101_26": "-1 * Max(Corr(TsRank($volume, 5), TsRank($high, 5), 5), 3)",
    "A101_27": "If(0.5 < Rank(Sum(Corr(Rank($volume), Rank($vwap), 6), 2) / 2), -1, 1)",
    "A101_28": "Scale(Corr($adv20, $low, 5) + ($high + $low) / 2 - $close)",
    "A101_30": "(1 - Rank(Sign($close - Ref($close, 1)) + Sign(Ref($close, 1) - Ref($close, 2)) + Sign(Ref($close, 2) - Ref($close, 3)))) * Sum($volume, 5) / Sum($volume, 20)",
    "A101_31": "Rank(Rank(Rank(DecayLinear(-1 * Rank(Rank(Delta($close, 10))), 10)))) + Rank(-1 * Delta($close, 3)) + Sign(Scale(Corr($adv20, $low, 12)))",
    "A101_32": "Scale(Sum($close, 7) / 7 - $close) + 20 * Scale(Corr($vwap, Ref($close, 5), 230))",
    "A101_33": "Rank(-1 * SignedPower(1 - $open / $close, 1))",
    "A101_34": "Rank(1 - Rank(Std($returns, 2) / Std($returns, 5))) + Rank(1 - Rank(Delta($close, 1)))",
    "A101_35": "TsRank($volume, 32) * (1 - TsRank($close + $high - $low, 16)) * (1 - TsRank($returns, 32))",
    "A101_37": "Rank(Corr(Ref($open - $close, 1), $close, 200)) + Rank($open - $close)",
    "A101_38": "(-1 * Rank(TsRank($close, 10))) * Rank($close / $open)",
    "A101_39": "(-1 * Rank(Delta($close, 7) * (1 - Rank(DecayLinear($volume / $adv20, 9))))) * (1 + Rank(Sum($returns, 250)))",
    "A101_40": "(-1 * Rank(Std($high, 10))) * Corr($high, $volume, 10)",
    "A101_41": "SignedPower($high * $low, 0.5) - $vwap",
    "A101_42": "Rank($vwap - $close) / (Rank($vwap + $close) + 1e-9)",
    "A101_43": "TsRank($volume / $adv20, 20) * TsRank(-1 * Delta($close, 7), 8)",
    "A101_44": "-1 * Corr($high, Rank($volume), 5)",
    "A101_47": "(Rank(1 / $close) * $volume / $adv20) * ($high * Rank($high - $close)) / (Sum($high, 5) / 5)",
    "A101_49": "If(-1 * Delta($close, 7) < -0.1, 1, -1 * ($close - Ref($close, 7)))",
    "A101_50": "-1 * Max(Rank(Corr(Rank($volume), Rank($vwap), 5)), 5)",
    "A101_51": "If(-1 * Max($close, 7) < -0.05, 1, -1 * ($close - Ref($close, 7)))",
    "A101_52": "(-1 * Min($low, 5) + Ref(Min($low, 5), 5)) * Rank((Sum($returns, 240) - Sum($returns, 20)) / 220) * TsRank($volume, 5)",
    "A101_55": "-1 * Corr(Rank(($close - Min($low, 12)) / (Max($high, 12) - Min($low, 12) + 1e-9)), Rank($volume), 6)",
    "A101_57": "-1 * ($close - $vwap) / (DecayLinear(Rank(TsArgMax($close, 30)), 2) + 1e-9)",
    "A101_60": "-1 * (2 * Scale(Rank((($close - $low) - ($high - $close)) / ($high - $low + 1e-9) * $volume)) - Scale(Rank(TsArgMax($close, 10))))",
    "A101_61": "If(Rank($vwap - Min($vwap, 16)) < Rank(Corr($vwap, $adv180, 18)), 1, 0)",
    "A101_62": "-1 * If(Rank(Corr($vwap, Sum($adv20, 22), 10)) < Rank(Rank($open) + Rank($open) < Rank(($high + $low) / 2) + Rank($high)), 1, 0)",
    "A101_64": "-1 * If(Rank(Corr(Sum($open * 0.178404 + $low * 0.821596, 13), Sum($adv120, 13), 17)) < Rank(Delta(($high + $low) / 2 * 0.178404 + $vwap * 0.821596, 4)), 1, 0)",
    "A101_65": "-1 * If(Rank(Corr($open * 0.00817205 + $vwap * 0.99182795, Sum($adv60, 9), 6)) < Rank($open - Min($open, 14)), 1, 0)",
    "A101_66": "-1 * (Rank(DecayLinear(Delta($vwap, 4), 7)) + TsRank(DecayLinear(($low - $vwap) / ($open - ($high + $low) / 2 + 1e-9), 11), 7))",
    "A101_68": "-1 * If(TsRank(Corr(Rank($high), Rank($adv15), 9), 14) < Rank(Delta($close * 0.518371 + $low * 0.481629, 1)), 1, 0)",
    "A101_71": "If(TsRank(DecayLinear(Corr(TsRank($close, 3), TsRank($adv180, 12), 18), 4), 16) > TsRank(DecayLinear(SignedPower(Rank($low + $open - $vwap - $vwap), 2), 16), 4), TsRank(DecayLinear(Corr(TsRank($close, 3), TsRank($adv180, 12), 18), 4), 16), TsRank(DecayLinear(SignedPower(Rank($low + $open - $vwap - $vwap), 2), 16), 4))",
    "A101_72": "Rank(DecayLinear(Corr(($high + $low) / 2, $adv40, 9), 10)) / (Rank(DecayLinear(Corr(TsRank($vwap, 4), TsRank($volume, 19), 7), 3)) + 1e-9)",
    "A101_73": "-1 * If(Rank(DecayLinear(Delta($vwap, 5), 3)) > TsRank(DecayLinear(-1 * Delta($open * 0.147155 + $low * 0.852845, 2) / ($open * 0.147155 + $low * 0.852845 + 1e-9), 3), 17), Rank(DecayLinear(Delta($vwap, 5), 3)), TsRank(DecayLinear(-1 * Delta($open * 0.147155 + $low * 0.852845, 2) / ($open * 0.147155 + $low * 0.852845 + 1e-9), 3), 17))",
    "A101_74": "-1 * If(Rank(Corr($close, Sum($adv30, 37), 15)) < Rank(Corr(Rank($high * 0.0261661 + $vwap * 0.9738339), Rank($volume), 11)), 1, 0)",
    "A101_75": "If(Rank(Corr($vwap, $volume, 4)) < Rank(Corr(Rank($low), Rank($adv50), 12)), 1, 0)",
    "A101_77": "If(Rank(DecayLinear(($high + $low) / 2 + $high - $vwap - $high, 20)) < Rank(DecayLinear(Corr(($high + $low) / 2, $adv40, 3), 6)), Rank(DecayLinear(($high + $low) / 2 + $high - $vwap - $high, 20)), Rank(DecayLinear(Corr(($high + $low) / 2, $adv40, 3), 6)))",
    "A101_78": "SignedPower(Rank(Corr(Sum($low * 0.352233 + $vwap * 0.647767, 20), Sum($adv40, 20), 7)), Rank(Corr(Rank($vwap), Rank($volume), 6)))",
    "A101_81": "-1 * If(Rank(Log(Product(Rank(SignedPower(Rank(Corr($vwap, Sum($adv10, 50), 8)), 4)), 15))) < Rank(Corr(Rank($vwap), Rank($volume), 5)), 1, 0)",
    "A101_83": "(Rank(Ref(($high - $low) / (Sum($close, 5) / 5 + 1e-9), 2)) * Rank(Rank($volume))) / (($high - $low) / (Sum($close, 5) / 5 + 1e-9) / ($vwap - $close + 1e-9))",
    "A101_101": "($close - $open) / ($high - $low + 0.001)",
}
ALPHA101 = {k: {"expr": v,
                "desc": f"Alpha101 原式 #{k.split('_')[1]}（公开研报转写，闸门候选未准入）",
                "family": "Alpha101候选"}
            for k, v in ALPHA101.items()}
