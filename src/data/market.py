# -*- coding: utf-8 -*-
"""
行情数据：日/周/月线、分钟线、实时快照；支持批量增量更新。
- 个股 / ETF：akshare 东财接口（快、字段全）
- 指数：baostock（东财指数接口分页很慢）
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from config import DEFAULT_START_DATE, REQUEST_INTERVAL
from src.data.storage import ParquetStore
from src.data.sources import ak, ensure_bs_login, bs_to_df, retry_call
from src.data.schema import (
    normalize_code, to_bs_code, to_sina_code, kline_file_name,
    standardize_ak_daily, standardize_ak_min, standardize_bs_daily,
    standardize_sina_daily,
)

_VALID_ASSET = ("stock", "etf", "index")
_VALID_PERIOD = ("daily", "weekly", "monthly")
_AK_PERIOD = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
_BS_FREQ = {"daily": "d", "weekly": "w", "monthly": "m"}


# ---------------------------------------------------------------- 单标的 K 线
_BS_ADJUST_FLAG = {"qfq": "2", "hfq": "1", "none": "3"}


def fetch_kline(
    symbol: str,
    start: str = DEFAULT_START_DATE,
    end: str | None = None,
    asset: str = "stock",
    period: str = "daily",
    adjust: str = "qfq",
    source: str = "auto",
    store: ParquetStore | None = None,
) -> pd.DataFrame:
    """
    抓取单标的 K 线并增量写入仓库，返回标准化 DataFrame。
    symbol: 6 位代码；start/end: YYYYMMDD；asset: stock/etf/index；
    period: daily/weekly/monthly；adjust: qfq(前复权)/hfq(后复权)/""(不复权)。
    指数无复权概念，自动忽略 adjust。

    source: "auto"(默认，个股/ETF 走东财) / "bs"(个股走 baostock，
    覆盖已退市股的历史行情，东财/新浪对退市股无数据)。
    """
    store = store or ParquetStore()
    code = normalize_code(symbol)
    end = end or pd.Timestamp.now().strftime("%Y%m%d")
    if asset not in _VALID_ASSET:
        raise ValueError(f"asset 仅支持 {_VALID_ASSET}")
    if period not in _VALID_PERIOD:
        raise ValueError(f"period 仅支持 {_VALID_PERIOD}")
    if asset == "index":
        source = "bs"
    if asset == "etf" and source == "bs":
        raise ValueError("ETF 不支持 baostock 源")
    # 复权口径落文件名：指数无复权概念，统一记 none；qfq/hfq 分文件隔离
    adj = "none" if asset == "index" else (adjust or "none")

    if source == "bs":
        bs = ensure_bs_login()
        bs_code = to_bs_code(code, is_index=(asset == "index"))
        fields = "date,code,open,high,low,close,volume,amount,turn,pctChg"
        flag = "3" if asset == "index" else _BS_ADJUST_FLAG.get(adj, "3")
        rs = bs.query_history_k_data_plus(
            bs_code, fields,
            start_date=f"{start[:4]}-{start[4:6]}-{start[6:]}",
            end_date=f"{end[:4]}-{end[4:6]}-{end[6:]}",
            frequency=_BS_FREQ[period], adjustflag=flag,
        )
        raw = bs_to_df(rs)
        if raw.empty:
            return pd.DataFrame()
        std = standardize_bs_daily(raw, code)
        src = "baostock"
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
        src = "akshare"

    kind = period  # daily / weekly / monthly
    name = kline_file_name(code, adj)
    total = store.upsert(std, kind, name, keys=["symbol", "date"],
                         source=src, date_col="date", adjust=adj)
    std.attrs["total_rows"] = total
    return std


# 便捷别名
def fetch_daily(symbol, start=DEFAULT_START_DATE, end=None,
                asset="stock", adjust="qfq", store=None) -> pd.DataFrame:
    return fetch_kline(symbol, start, end, asset, "daily", adjust, store)


# ---------------------------------------------------------------- 个股日线（新浪）
def fetch_daily_sina(
    symbol: str,
    start: str = DEFAULT_START_DATE,
    end: str | None = None,
    adjust: str = "qfq",
    store: ParquetStore | None = None,
) -> pd.DataFrame:
    """
    抓取个股日线（新浪 stock_zh_a_daily，稳定后端，东财被限流时使用）。
    返回标准化 DataFrame 并增量写入 daily。字段含 OHLCV/amount/turnover。
    """
    store = store or ParquetStore()
    code = normalize_code(symbol)
    end = end or pd.Timestamp.now().strftime("%Y%m%d")
    sina_code = to_sina_code(code)
    raw = retry_call(
        ak.stock_zh_a_daily, symbol=sina_code,
        start_date=start, end_date=end, adjust=adjust,
        label=f"sina:{code}",
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    std = standardize_sina_daily(raw, code)
    adj = adjust or "none"
    name = kline_file_name(code, adj)
    total = store.upsert(std, "daily", name, keys=["symbol", "date"],
                         source="akshare/sina", date_col="date", adjust=adj)
    std.attrs["total_rows"] = total
    return std


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
    adj = adjust or "none"
    name = f"{code}__{freq}__{adj}"
    store.upsert(std, "minute", name, keys=["symbol", "datetime"],
                 source="akshare", date_col="datetime", adjust=adj)
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
def get_kline(symbol: str, period: str = "daily", adjust: str = "qfq",
              store: ParquetStore | None = None) -> pd.DataFrame:
    """从本地仓库读取 K 线。adjust: qfq/hfq/none（指数数据用 none）。"""
    store = store or ParquetStore()
    name = kline_file_name(symbol, adjust)
    return store.read(period, name)


def get_local_last_date(symbol: str, period: str = "daily", adjust: str = "qfq",
                        store: ParquetStore | None = None):
    """本地数据的最后日期，无数据返回 None。"""
    store = store or ParquetStore()
    code = normalize_code(symbol)
    name = kline_file_name(code, adjust)
    if not store.exists(period, name):
        return None
    df = store.read(period, name, columns=["date"])
    return pd.to_datetime(df["date"]).max()


def last_closed_trade_date(store: ParquetStore | None = None):
    """最近一个已收盘交易日（16:00 收盘后才计入当天；无交易日历返回 None）。"""
    store = store or ParquetStore()
    if not store.exists("meta", "trade_calendar"):
        return None
    cal = pd.to_datetime(store.read("meta", "trade_calendar")["date"])
    now = pd.Timestamp.now()
    cutoff = now.normalize() if now.hour >= 16 else now.normalize() - pd.Timedelta(days=1)
    closed = cal[cal <= cutoff]
    return closed.max() if len(closed) else None


# ---------------------------------------------------------------- 批量增量更新
def update_kline_batch(
    symbols: list[str],
    asset: str = "stock",
    period: str = "daily",
    adjust: str = "qfq",
    full_start: str = DEFAULT_START_DATE,
    source: str = "em",
    store: ParquetStore | None = None,
    workers: int = 8,
) -> dict:
    """
    【并发】批量增量更新 K 线：
      - 本地最后日期已 >= 最近收盘交易日的标的直接跳过（不发请求），盘后/周末重跑秒级；
      - 其余标的用线程池并发抓取（IO 密集，workers 控制并发，默认 8）；
      - 注册表在 batch_write 内缓冲，全部结束统一写盘一次（避免并发写竞争）。
    source: "em"(东财，默认) / "sina"(新浪，东财被限流时用，仅个股)。
    返回 {symbol: 新增行数或"uptodate"}，失败标的收集在 __failed__ 中。
    """
    store = store or ParquetStore()
    report: dict = {"__failed__": []}
    n = len(symbols)
    last_closed = last_closed_trade_date(store)

    def _one(sym):
        code = normalize_code(sym)
        try:
            last = get_local_last_date(code, period, adjust, store)
            if last is not None and last_closed is not None and last >= last_closed:
                return code, "uptodate"
            if last is not None:
                start = (last + pd.Timedelta(days=1)).strftime("%Y%m%d")
            else:
                start = full_start
            if source == "sina":
                df = fetch_daily_sina(code, start=start, adjust=adjust, store=store)
            else:
                df = fetch_kline(code, start=start, asset=asset,
                                 period=period, adjust=adjust, store=store)
            return code, len(df)
        except Exception as e:  # noqa: BLE001
            return code, f"__ERR__:{e}"

    done = 0
    with store.batch_write():
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futures = [ex.submit(_one, s) for s in symbols]
            for fut in as_completed(futures):
                code, res = fut.result()
                done += 1
                if isinstance(res, str) and res.startswith("__ERR__:"):
                    report["__failed__"].append((code, res[len("__ERR__:"):]))
                else:
                    report[code] = res
                if done % 20 == 0 or done == n:
                    print(f"  进度 {done}/{n}，失败 {len(report['__failed__'])}", flush=True)
    return report
