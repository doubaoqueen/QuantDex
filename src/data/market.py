# -*- coding: utf-8 -*-
"""
行情数据：日/周/月线、分钟线、实时快照；支持批量增量更新。
- 个股 / ETF：akshare 东财接口（快、字段全）
- 指数：baostock（东财指数接口分页很慢）
"""
import time
import pandas as pd

from config import DEFAULT_START_DATE, REQUEST_INTERVAL
from src.data.storage import ParquetStore
from src.data.sources import ak, ensure_bs_login, bs_to_df, retry_call
from src.data.schema import (
    normalize_code, to_bs_code,
    standardize_ak_daily, standardize_ak_min, standardize_bs_daily,
)

_VALID_ASSET = ("stock", "etf", "index")
_VALID_PERIOD = ("daily", "weekly", "monthly")
_AK_PERIOD = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
_BS_FREQ = {"daily": "d", "weekly": "w", "monthly": "m"}


# ---------------------------------------------------------------- 单标的 K 线
def fetch_kline(
    symbol: str,
    start: str = DEFAULT_START_DATE,
    end: str | None = None,
    asset: str = "stock",
    period: str = "daily",
    adjust: str = "qfq",
    store: ParquetStore | None = None,
) -> pd.DataFrame:
    """
    抓取单标的 K 线并增量写入仓库，返回标准化 DataFrame。
    symbol: 6 位代码；start/end: YYYYMMDD；asset: stock/etf/index；
    period: daily/weekly/monthly；adjust: qfq(前复权)/hfq(后复权)/""(不复权)。
    指数无复权概念，自动忽略 adjust。
    """
    store = store or ParquetStore()
    code = normalize_code(symbol)
    end = end or pd.Timestamp.now().strftime("%Y%m%d")
    if asset not in _VALID_ASSET:
        raise ValueError(f"asset 仅支持 {_VALID_ASSET}")
    if period not in _VALID_PERIOD:
        raise ValueError(f"period 仅支持 {_VALID_PERIOD}")

    if asset == "index":
        bs = ensure_bs_login()
        bs_code = to_bs_code(code, is_index=True)
        fields = "date,code,open,high,low,close,volume,amount,turn,pctChg"
        rs = bs.query_history_k_data_plus(
            bs_code, fields,
            start_date=f"{start[:4]}-{start[4:6]}-{start[6:]}",
            end_date=f"{end[:4]}-{end[4:6]}-{end[6:]}",
            frequency=_BS_FREQ[period], adjustflag="3",
        )
        raw = bs_to_df(rs)
        if raw.empty:
            return pd.DataFrame()
        std = standardize_bs_daily(raw, code)
    else:
        if asset == "stock":
            fn = ak.stock_zh_a_hist
        else:
            fn = ak.fund_etf_hist_em
        raw = retry_call(
            fn, symbol=code, period=_AK_PERIOD[period],
            start_date=start, end_date=end, adjust=adjust,
            label=f"{asset}:{code}",
        )
        if raw is None or raw.empty:
            return pd.DataFrame()
        std = standardize_ak_daily(raw, code)

    kind = period  # daily / weekly / monthly
    total = store.upsert(std, kind, code, keys=["symbol", "date"],
                         source="akshare" if asset != "index" else "baostock",
                         date_col="date")
    std.attrs["total_rows"] = total
    return std


# 便捷别名
def fetch_daily(symbol, start=DEFAULT_START_DATE, end=None,
                asset="stock", adjust="qfq", store=None) -> pd.DataFrame:
    return fetch_kline(symbol, start, end, asset, "daily", adjust, store)


# ---------------------------------------------------------------- 分钟线
def fetch_minute(
    symbol: str,
    freq: str = "5",
    asset: str = "stock",
    adjust: str = "qfq",
    store: ParquetStore | None = None,
) -> pd.DataFrame:
    """
    抓取分钟线（东财，返回最近若干交易日，通常约 1-2 个月）。
    freq: 1/5/15/30/60；个股 stock_zh_a_hist_min_em，ETF 走 fund_etf_hist_min_em。
    """
    store = store or ParquetStore()
    code = normalize_code(symbol)
    if asset == "stock":
        raw = retry_call(ak.stock_zh_a_hist_min_em, symbol=code,
                         period=str(freq), adjust=adjust,
                         label=f"min:{code}")
    elif asset == "etf":
        if not hasattr(ak, "fund_etf_hist_min_em"):
            raise RuntimeError("当前 akshare 版本不支持 ETF 分钟线")
        raw = retry_call(ak.fund_etf_hist_min_em, symbol=code,
                         period=str(freq), adjust=adjust,
                         label=f"etf-min:{code}")
    else:
        raise ValueError("分钟线仅支持 stock / etf")
    if raw is None or raw.empty:
        return pd.DataFrame()
    std = standardize_ak_min(raw, code)
    name = f"{code}__{freq}"
    store.upsert(std, "minute", name, keys=["symbol", "datetime"],
                 source="akshare", date_col="datetime")
    return std


# ---------------------------------------------------------------- 实时快照
def fetch_spot(asset: str = "stock", store: ParquetStore | None = None,
               save: bool = False) -> pd.DataFrame:
    """
    全市场实时行情快照（东财）。
    asset: stock / etf；save=True 时覆盖落盘 meta/spot_{asset}。
    """
    store = store or ParquetStore()
    if asset == "stock":
        df = retry_call(ak.stock_zh_a_spot_em, label="stock_spot")
    elif asset == "etf":
        df = retry_call(ak.fund_etf_spot_em, label="etf_spot")
    else:
        raise ValueError("快照仅支持 stock / etf")
    if "代码" in df.columns:
        df = df.rename(columns={"代码": "symbol"})
        df["symbol"] = df["symbol"].map(normalize_code)
    if save:
        store.write(df, "meta", f"spot_{asset}", source="akshare")
    return df


# ---------------------------------------------------------------- 本地读取
def get_kline(symbol: str, period: str = "daily",
              store: ParquetStore | None = None) -> pd.DataFrame:
    """从本地仓库读取 K 线。"""
    store = store or ParquetStore()
    return store.read(period, normalize_code(symbol))


def get_local_last_date(symbol: str, period: str = "daily",
                        store: ParquetStore | None = None):
    """本地数据的最后日期，无数据返回 None。"""
    store = store or ParquetStore()
    code = normalize_code(symbol)
    if not store.exists(period, code):
        return None
    df = store.read(period, code, columns=["date"])
    return pd.to_datetime(df["date"]).max()


# ---------------------------------------------------------------- 批量增量更新
def update_kline_batch(
    symbols: list[str],
    asset: str = "stock",
    period: str = "daily",
    adjust: str = "qfq",
    full_start: str = DEFAULT_START_DATE,
    store: ParquetStore | None = None,
) -> dict:
    """
    批量增量更新 K 线：已有数据的标的只抓最后日期之后，新标的从 full_start 抓。
    返回 {symbol: 新增/状态}，失败标的收集在返回值的 __failed__ 中。
    """
    store = store or ParquetStore()
    report: dict = {"__failed__": []}
    n = len(symbols)
    for i, sym in enumerate(symbols, 1):
        code = normalize_code(sym)
        try:
            last = get_local_last_date(code, period, store)
            if last is not None:
                start = (last + pd.Timedelta(days=1)).strftime("%Y%m%d")
                if pd.Timestamp(start) > pd.Timestamp.now().normalize():
                    report[code] = "uptodate"
                    continue
            else:
                start = full_start
            df = fetch_kline(code, start=start, asset=asset,
                             period=period, adjust=adjust, store=store)
            report[code] = len(df)
        except Exception as e:  # noqa: BLE001
            report["__failed__"].append((code, str(e)))
        if i % 20 == 0 or i == n:
            print(f"  进度 {i}/{n}，失败 {len(report['__failed__'])}")
        time.sleep(REQUEST_INTERVAL)
    return report
