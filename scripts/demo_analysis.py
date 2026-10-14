# -*- coding: utf-8 -*-
"""
演示：读取本地数据 -> 技术指标 -> 数据质检 -> 收益概览 -> 回测绩效。
用法：python scripts/demo_analysis.py [代码，默认510300]
"""
import _bootstrap  # noqa: F401
import sys

from src.data.storage import ParquetStore
from src.data.market import get_kline
from src.analysis.indicators import add_indicators
from src.analysis.quality import quality_report
from src.analysis.report import symbol_overview, yearly_returns, store_coverage
from src.strategies.dual_ma import dual_ma_signal
from src.backtest.engine import run_backtest
from src.backtest.metrics import performance


# 需要按百分比显示的指标
_PCT_KEYS = {
    "区间总收益", "年化收益", "年化波动", "最大回撤", "基准区间收益", "超额收益(区间)",
    "总收益率", "年化收益率", "年化波动率", "最大回撤", "基准总收益率",
    "超额总收益", "年化超额收益", "胜率", "平均每笔收益", "平均盈利", "平均亏损",
}
# 倍数/数值（保留2位小数），天数/笔数（整数）
_NUM2_KEYS = {"夏普比率", "Sortino比率", "Calmar比率", "盈亏比", "信息比率",
              "与基准相关系数", "相对基准Beta"}
_INT_KEYS = {"交易笔数", "区间交易日"}
_NUM1_KEYS = {"平均持仓天数"}


def fmt(key, v):
    if v is None or (isinstance(v, float) and v != v):  # NaN
        if key == "最大回撤持续(交易日)":
            return "未恢复"
        return v
    if key in _PCT_KEYS:
        return f"{v:.2%}"
    if key in _NUM2_KEYS:
        return f"{v:.2f}"
    if key in _NUM1_KEYS:
        return f"{v:.1f}"
    if key in _INT_KEYS:
        return int(v)
    if key == "最大回撤持续(交易日)":
        return int(v)
    if key.startswith("近"):  # 近N日收益
        return f"{v:.2%}"
    return v


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "510300"
    store = ParquetStore()

    df = get_kline(symbol, store=store)
    print(f">> {symbol} 本地日线 {len(df)} 行：{df['date'].min().date()} ~ {df['date'].max().date()}")

    print("\n>> 数据质检：")
    qsum, _ = quality_report(df, store=store, symbol=symbol)
    for k, v in qsum.items():
        print(f"  {k}: {v}")

    print("\n>> 收益概览：")
    ov = symbol_overview(df)
    for k, v in ov.items():
        print(f"  {k}: {fmt(k, v)}")

    print("\n>> 分年度收益：")
    for y, r in yearly_returns(df).items():
        print(f"  {y}: {r:.2%}")

    print("\n>> 双均线(5,20)回测：")
    sig = dual_ma_signal(df, fast=5, slow=20)
    result, trades = run_backtest(sig, stamp_duty=0.0)
    stats = performance(result, trades)
    for k, v in stats.items():
        print(f"  {k}: {fmt(k, v)}")

    print("\n>> 数据仓库覆盖：")
    print(store_coverage(store).to_string())

    # 自动出图（失败不影响控制台结果）
    try:
        from src.analysis.plot import plot_kline, plot_backtest, plot_yearly_returns
        p1 = plot_kline(df, name=symbol, title=f"{symbol} 收盘价与均线")
        p2 = plot_backtest(result, title=f"{symbol} 双均线(5,20)回测", name=symbol)
        p3 = plot_yearly_returns(df, title=f"{symbol} 分年度收益", name=symbol)
        print("\n>> 图表已保存：")
        for p in (p1, p2, p3):
            print(f"   {p}")
    except Exception as e:  # noqa: BLE001
        print(f"\n>> [warn] 出图失败：{e}")

    # 带指标的完整 DataFrame 可用于后续研究
    _ = add_indicators(df)


if __name__ == "__main__":
    main()
