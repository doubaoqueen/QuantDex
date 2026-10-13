# -*- coding: utf-8 -*-
"""
财务数据：
- akshare 财务摘要（宽表转长表，80+ 指标 × 全部报告期）
- baostock 季频六件套：profit 盈利 / operation 营运 / growth 成长 /
  balance 偿债(资产负债) / cashflow 现金流 / dupont 杜邦
- baostock 业绩快报 / 业绩预告
"""
import pandas as pd

from src.data.storage import ParquetStore
from src.data.sources import ak, ensure_bs_login, bs_to_df, retry_call
from src.data.schema import normalize_code, to_bs_code

_BS_FIN_LOADERS = None  # 延迟初始化（需要先登录拿 bs 模块）


# ---------------------------------------------------------------- akshare 摘要
def fetch_financial_abstract(symbol: str,
                             store: ParquetStore | None = None) -> pd.DataFrame:
    """财务摘要 -> 长表 symbol,report_date,category,item,value。"""
    store = store or ParquetStore()
    code = normalize_code(symbol)
    df = retry_call(ak.stock_financial_abstract, symbol=code,
                    label="fin_abstract")
    long = df.melt(id_vars=["选项", "指标"], var_name="report_date",
                   value_name="value")
    long = long.rename(columns={"选项": "category", "指标": "item"})
    long["report_date"] = pd.to_datetime(long["report_date"],
                                         format="%Y%m%d", errors="coerce")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long.insert(0, "symbol", code)
    long = long.dropna(subset=["report_date"])
    long = long.sort_values(["report_date", "item"]).reset_index(drop=True)
    store.write(long, "fin_abstract", code, source="akshare",
                date_col="report_date")
    return long


# ---------------------------------------------------------------- baostock 季频
def fetch_bs_financials(symbol: str, years: int = 3,
                        store: ParquetStore | None = None) -> dict:
    """
    抓取近 years 年 baostock 季频六件套，每类一个 parquet：
    fin_bs/{symbol}__profit 等。返回 {类型: 行数}。
    """
    store = store or ParquetStore()
    bs = ensure_bs_login()
    code = to_bs_code(normalize_code(symbol))
    this_year = pd.Timestamp.now().year

    loaders = {
        "profit": bs.query_profit_data,
        "operation": bs.query_operation_data,
        "growth": bs.query_growth_data,
        "balance": bs.query_balance_data,
        "cashflow": bs.query_cash_flow_data,
        "dupont": bs.query_dupont_data,
    }
    result = {}
    for ftype, fn in loaders.items():
        frames = []
        for year in range(this_year - years + 1, this_year + 1):
            for quarter in (1, 2, 3, 4):
                rs = fn(code=code, year=year, quarter=quarter)
                df = bs_to_df(rs)
                if not df.empty:
                    frames.append(df)
        if frames:
            out = pd.concat(frames, ignore_index=True)
            name = f"{normalize_code(symbol)}__{ftype}"
            store.write(out, "fin_bs", name, source="baostock", date_col="statDate")
            result[ftype] = len(out)
    return result


# ---------------------------------------------------------------- 快报/预告
def fetch_express_forecast(symbol: str, years: int = 2,
                           store: ParquetStore | None = None) -> dict:
    """业绩快报与业绩预告（baostock，按日期范围）。"""
    store = store or ParquetStore()
    bs = ensure_bs_login()
    code = to_bs_code(normalize_code(symbol))
    this_year = pd.Timestamp.now().year
    start = f"{this_year - years + 1}-01-01"
    end = pd.Timestamp.now().strftime("%Y-%m-%d")
    result = {}

    rs1 = bs.query_performance_express_report(code, start_date=start, end_date=end)
    df1 = bs_to_df(rs1)
    if not df1.empty:
        store.write(df1, "fin_bs", f"{normalize_code(symbol)}__express",
                    source="baostock", date_col="statDate")
        result["express"] = len(df1)

    rs2 = bs.query_forecast_report(code, start_date=start, end_date=end)
    df2 = bs_to_df(rs2)
    if not df2.empty:
        store.write(df2, "fin_bs", f"{normalize_code(symbol)}__forecast",
                    source="baostock", date_col="statDate")
        result["forecast"] = len(df2)
    return result


# ---------------------------------------------------------------- 分红送配
def fetch_dividend(symbol: str, years: int = 6,
                   store: ParquetStore | None = None) -> pd.DataFrame:
    """
    近年分红送配记录（baostock，按报告年度）。
    含股权登记日、除权除息日、每股税前/税后现金红利、送转股方案。
    """
    store = store or ParquetStore()
    bs = ensure_bs_login()
    code = to_bs_code(normalize_code(symbol))
    this_year = pd.Timestamp.now().year
    frames = []
    for year in range(this_year - years + 1, this_year + 1):
        rs = bs.query_dividend_data(code=code, year=year, yearType="report")
        df = bs_to_df(rs)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    store.write(out, "fin_bs", f"{normalize_code(symbol)}__dividend",
                source="baostock", date_col="dividOperateDate")
    return out


# ---------------------------------------------------------------- 复权因子
def fetch_adjust_factor(symbol: str, start: str = "2010-01-01",
                        end: str | None = None,
                        store: ParquetStore | None = None) -> pd.DataFrame:
    """复权因子历史（baostock，每次除权除息一条；前复权/后复权/累计因子）。"""
    store = store or ParquetStore()
    bs = ensure_bs_login()
    code = to_bs_code(normalize_code(symbol))
    end = end or pd.Timestamp.now().strftime("%Y-%m-%d")
    rs = bs.query_adjust_factor(code=code, start_date=start, end_date=end)
    df = bs_to_df(rs)
    if not df.empty:
        store.write(df, "fin_bs", f"{normalize_code(symbol)}__adjfactor",
                    source="baostock", date_col="dividOperateDate")
    return df


def fetch_all_financials(symbol: str, years: int = 3,
                         store: ParquetStore | None = None) -> dict:
    """一只股票的全套财务数据（摘要 + 季频六件套 + 快报预告 + 分红 + 复权因子）。"""
    store = store or ParquetStore()
    result = {}
    result["abstract_rows"] = len(fetch_financial_abstract(symbol, store))
    result["baostock"] = fetch_bs_financials(symbol, years, store)
    result["express_forecast"] = fetch_express_forecast(symbol, max(2, years - 1), store)
    result["dividend_rows"] = len(fetch_dividend(symbol, years + 3, store))
    result["adjfactor_rows"] = len(fetch_adjust_factor(symbol, store=store))
    return result
