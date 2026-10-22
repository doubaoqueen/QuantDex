# 因子 IC 与 Newey-West t

## 一句话
IC = 因子排名与未来收益排名的相关性；NW-t = 考虑重叠持有期后的显著性。

## 为什么重要
普通 t 值假设样本独立，但 20 日持有期的 IC 高度重叠 → t 值虚高；NW 修正自相关。

## 项目里在哪
- `src/analysis/factor_test.py`：`rankic_series`、`_newey_west_tstat`
- 结果：`factor_results/ic_summary.parquet`

## 结论备忘
低换手 NW-t -4.61、低波 -4.47（2018–2026，PIT 口径）；市值中性化后仍显著。

## 我的疑问
