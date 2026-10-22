# 复权与 PIT

## 一句话
复权解决"价格可比"，PIT 解决"当时真实存在"。

## 为什么重要
- 不复权：除权日价格跳空，收益算错；
- 用当前成分回测过去：只看活到最后的赢家 → 幸存者偏差。

## 项目里在哪
- 复权：`market/daily/{symbol}__{qfq|hfq|none}.parquet`；禁止混写
- PIT：`meta/pit_constituents_000300.parquet`（in_date/out_date）
- 退市股：`scripts/04_factor_research.py` 用 baostock 补齐

## 我的疑问
（写在这里）
