# -*- coding: utf-8 -*-
"""
标的池与基础数据：股票/ETF/指数/可转债列表、交易日历、全量证券、指数成分股。
所有结果落 Parquet 仓库的 meta 分区。
"""
import time
import pandas as pd

from config import REQUEST_INTERVAL
from src.data.storage import ParquetStore
from src.data.sources import ak, ensure_bs_login, bs_to_df, retry_call
from src.data.schema import normalize_code


# ---------------------------------------------------------------- A股股票列表
def fetch_stock_list(store: ParquetStore | None = None) -> pd.DataFrame:
    """A股全部股票代码与名称（akshare，约5500+）。返回 symbol,name。"""
    store = store or ParquetStore()
    df = retry_call(ak.stock_info_a_code_name, label="stock_info_a_code_name")
    df = df.rename(columns={"code": "symbol"})
    df["symbol"] = df["symbol"].map(normalize_code)
    df = df[["symbol", "name"]].drop_duplicates("symbol").sort_values("symbol")
    store.write(df, "meta", "stock_list", source="akshare", date_col=None)
    return df


# ---------------------------------------------------------------- ETF 列表
def fetch_etf_list(store: ParquetStore | None = None) -> pd.DataFrame:
    """全部 ETF 快照即列表（东财，约1600+），保留核心字段。"""
    store = store or ParquetStore()
    df = retry_call(ak.fund_etf_spot_em, label="fund_etf_spot_em")
    rename = {
        "代码": "symbol", "名称": "name", "最新价": "last", "涨跌幅": "pct_chg",
        "成交额": "amount", "换手率": "turnover", "流通市值": "circ_mv",
        "总市值": "total_mv", "数据日期": "date",
    }
    df = df.rename(columns=rename)
    df["symbol"] = df["symbol"].map(normalize_code)
    keep = ["symbol", "name", "last", "pct_chg", "amount", "turnover",
            "circ_mv", "total_mv", "date"]
    df = df[[c for c in keep if c in df.columns]]
    df = df.drop_duplicates("symbol").sort_values("symbol")
    store.write(df, "meta", "etf_list", source="akshare", date_col=None)
    return df


# ---------------------------------------------------------------- 指数列表
def fetch_index_list(store: ParquetStore | None = None) -> pd.DataFrame:
    """全部指数行情即列表（东财，约260+）。"""
    store = store or ParquetStore()
    df = retry_call(ak.stock_zh_index_spot_em, label="stock_zh_index_spot_em")
    rename = {"代码": "symbol", "名称": "name", "最新价": "last",
              "涨跌幅": "pct_chg", "成交额": "amount"}
    df = df.rename(columns=rename)
    df["symbol"] = df["symbol"].map(normalize_code)
    df = df[["symbol", "name", "last", "pct_chg", "amount"]]
    df = df.drop_duplicates("symbol").sort_values("symbol")
    store.write(df, "meta", "index_list", source="akshare", date_col=None)
    return df


# ---------------------------------------------------------------- 可转债列表
def fetch_bond_list(store: ParquetStore | None = None) -> pd.DataFrame:
    """全部可转债（含已发行/待上市，东财，约1000+）。"""
    store = store or ParquetStore()
    df = retry_call(ak.bond_zh_cov, label="bond_zh_cov")
    rename = {
        "债券代码": "symbol", "债券简称": "name", "上市时间": "list_date",
        "正股代码": "underlying", "正股简称": "underlying_name",
        "正股价": "underlying_price", "转股价": "convert_price",
        "转股价值": "convert_value", "债现价": "bond_price",
        "转股溢价率": "convert_premium", "发行规模": "issue_size", "信用评级": "rating",
    }
    df = df.rename(columns=rename)
    df["symbol"] = df["symbol"].map(normalize_code)
    keep = ["symbol", "name", "list_date", "underlying", "underlying_name",
            "underlying_price", "convert_price", "convert_value", "bond_price",
            "convert_premium", "issue_size", "rating"]
    df = df[[c for c in keep if c in df.columns]].drop_duplicates("symbol")
    df = df.sort_values("symbol")
    store.write(df, "meta", "bond_list", source="akshare", date_col=None)
    return df


# ---------------------------------------------------------------- 交易日历
def fetch_trade_calendar(store: ParquetStore | None = None) -> pd.DataFrame:
    """上交所/深交所历史交易日历（新浪，1990 至今）。返回 date 列。"""
    store = store or ParquetStore()
    df = retry_call(ak.tool_trade_date_hist_sina, label="tool_trade_date_hist_sina")
    df = df.rename(columns={"trade_date": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates().sort_values("date").reset_index(drop=True)
    store.write(df, "meta", "trade_calendar", source="akshare", date_col="date")
    return df


def is_trading_day(date, store: ParquetStore | None = None) -> bool:
    """判断某日是否为交易日。"""
    store = store or ParquetStore()
    cal = store.read("meta", "trade_calendar")
    return pd.Timestamp(date) in set(pd.to_datetime(cal["date"]))


# ---------------------------------------------------------------- baostock 全量证券
def fetch_bs_securities(store: ParquetStore | None = None) -> pd.DataFrame:
    """
    baostock 全量证券（股票+指数，含 IPO/退市日期、类型、状态）。
    type: 1=股票 2=指数 3=其他；status: 1=正常 0=退市/暂停。
    """
    store = store or ParquetStore()
    bs = ensure_bs_login()
    rs = bs.query_stock_basic(code_name="")
    df = bs_to_df(rs)
    df = df.rename(columns={
        "code": "bs_code", "code_name": "name", "ipoDate": "ipo_date",
        "outDate": "out_date", "type": "bs_type", "status": "status",
    })
    df["symbol"] = df["bs_code"].map(normalize_code)
    for c in ("bs_type", "status"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df[["symbol", "bs_code", "name", "ipo_date", "out_date", "bs_type", "status"]]
    df = df.drop_duplicates("bs_code").sort_values("bs_code").reset_index(drop=True)
    store.write(df, "meta", "bs_securities", source="baostock", date_col=None)
    return df


# ---------------------------------------------------------------- 行业分类
def fetch_industry(store: ParquetStore | None = None) -> pd.DataFrame:
    """全部 A股行业分类（baostock，证监会行业分类，约5500+）。"""
    store = store or ParquetStore()
    bs = ensure_bs_login()
    rs = bs.query_stock_industry()
    df = bs_to_df(rs).rename(columns={
        "code": "bs_code", "code_name": "name",
        "industry": "industry", "industryClassification": "industry_class",
        "updateDate": "update_date",
    })
    df["symbol"] = df["bs_code"].map(normalize_code)
    df = df[["symbol", "bs_code", "name", "industry",
             "industry_class", "update_date"]]
    df = df.drop_duplicates("bs_code").sort_values("bs_code").reset_index(drop=True)
    store.write(df, "meta", "industry", source="baostock")
    return df


# ---------------------------------------------------------------- 指数成分股
def fetch_index_constituents(index_code: str = "000300",
                             store: ParquetStore | None = None) -> pd.DataFrame:
    """
    指数成分股（中证指数公司 csindex），支持 000300/000905/000852/000016 等。
    返回 symbol,name,index_code。
    """
    store = store or ParquetStore()
    code = normalize_code(index_code)
    df = retry_call(ak.index_stock_cons_csindex, symbol=code,
                    label="index_stock_cons_csindex")
    df = df.rename(columns={"成分券代码": "symbol", "成分券名称": "name"})
    df["symbol"] = df["symbol"].map(normalize_code)
    df["index_code"] = code
    df = df[["index_code", "symbol", "name"]].drop_duplicates("symbol")
    df = df.sort_values("symbol").reset_index(drop=True)
    store.write(df, "meta", f"constituents_{code}", source="akshare/csindex")
    time.sleep(REQUEST_INTERVAL)
    return df


def init_universe(store: ParquetStore | None = None,
                  constituent_indexes=("000300", "000905", "000852")) -> dict:
    """一键初始化全部标的池与基础数据。"""
    store = store or ParquetStore()
    result = {}
    tasks = [
        ("stock_list", fetch_stock_list),
        ("etf_list", fetch_etf_list),
        ("index_list", fetch_index_list),
        ("bond_list", fetch_bond_list),
        ("trade_calendar", fetch_trade_calendar),
        ("bs_securities", fetch_bs_securities),
        ("industry", fetch_industry),
    ]
    for key, fn in tasks:
        df = fn(store)
        result[key] = len(df)
    for idx in constituent_indexes:
        df = fetch_index_constituents(idx, store)
        result[f"constituents_{idx}"] = len(df)
    return result
