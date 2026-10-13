# -*- coding: utf-8 -*-
"""
宏观经济数据：
- akshare：CPI、LPR
- baostock：CPI/PPI/GDP/PMI、货币供应量(月度)、存贷款基准利率、存款准备金率
"""
import pandas as pd

from src.data.storage import ParquetStore
from src.data.sources import ak, ensure_bs_login, bs_to_df, retry_call


# ---------------------------------------------------------------- akshare
def fetch_cpi(store: ParquetStore | None = None) -> pd.DataFrame:
    """CPI（月度，国家统计局口径，akshare）。"""
    store = store or ParquetStore()
    df = retry_call(ak.macro_china_cpi, label="cpi")
    df = df.rename(columns={"月份": "month"})
    store.write(df, "macro", "cpi", source="akshare")
    return df


def fetch_lpr(store: ParquetStore | None = None) -> pd.DataFrame:
    """LPR 贷款市场报价利率（含历史贷款基准利率，akshare）。"""
    store = store or ParquetStore()
    df = retry_call(ak.macro_china_lpr, label="lpr")
    df = df.rename(columns={"TRADE_DATE": "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    store.write(df, "macro", "lpr", source="akshare", date_col="date")
    return df


# ---------------------------------------------------------------- baostock
def _fetch_bs_macro_one(bs, func_name: str, out_name: str,
                        store: ParquetStore, use_range: bool = False) -> pd.DataFrame:
    """通用 baostock 宏观抓取。"""
    fn = getattr(bs, func_name, None)
    if fn is None:
        return pd.DataFrame()
    if use_range:
        end = pd.Timestamp.now().strftime("%Y-%m")
        rs = fn(start_date="1990-01", end_date=end)
    else:
        rs = fn()
    df = bs_to_df(rs)
    if df.empty:
        return df
    date_candidates = [c for c in ("statDate", "date", "month", "pubDate",
                                   "effectiveDate") if c in df.columns]
    store.write(df, "macro", out_name, source="baostock",
                date_col=date_candidates[0] if date_candidates else None)
    return df


def fetch_bs_macro(store: ParquetStore | None = None) -> dict:
    """
    抓取 baostock 宏观数据，返回 {名称: 行数}。
    注：baostock 0.9.4 无 CPI/PPI/GDP/PMI 接口，CPI 用 akshare（macro/cpi）。
    """
    store = store or ParquetStore()
    bs = ensure_bs_login()
    jobs = [
        ("query_money_supply_data_month", "money_supply_m", True),
        ("query_loan_rate_data", "loan_rate", False),
        ("query_deposit_rate_data", "deposit_rate", False),
        ("query_required_reserve_ratio_data", "rrr", False),
    ]
    result = {}
    for func_name, out_name, use_range in jobs:
        try:
            df = _fetch_bs_macro_one(bs, func_name, out_name, store, use_range)
            result[out_name] = len(df)
        except Exception as e:  # 单个接口失败不影响其余
            result[out_name] = f"FAIL: {e}"
    return result
