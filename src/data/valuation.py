# -*- coding: utf-8 -*-
"""估值数据：个股 PE/PB/PS/市值（百度股市通）、指数 PE/PB（乐咕乐股）。"""
import time
import pandas as pd

from config import REQUEST_INTERVAL
from src.data.storage import ParquetStore
from src.data.sources import ak, retry_call
from src.data.schema import normalize_code

# 百度股市通指标 -> 标准列名
_BAIDU_INDICATORS = {
    "总市值": "market_cap",
    "市盈率(TTM)": "pe_ttm",
    "市净率": "pb",
    "市销率(TTM)": "ps_ttm",
}


def fetch_stock_valuation(
    symbol: str,
    period: str = "全部",
    store: ParquetStore | None = None,
) -> pd.DataFrame:
    """
    个股估值历史（百度股市通）。
    period: 近一年/近三年/近五年/全部；返回 symbol,date,market_cap,pe_ttm,pb,ps_ttm。
    """
    store = store or ParquetStore()
    code = normalize_code(symbol)
    frames = []
    for indicator, col in _BAIDU_INDICATORS.items():
        try:
            df = retry_call(ak.stock_zh_valuation_baidu, symbol=code,
                            indicator=indicator, period=period,
                            label=f"val:{code}:{indicator}")
        except Exception:  # 单个指标失败不影响其余
            continue
        if df is None or df.empty:
            continue
        df = df.rename(columns={"value": col})[["date", col]]
        df["date"] = pd.to_datetime(df["date"])
        frames.append(df)
        time.sleep(REQUEST_INTERVAL)
    if not frames:
        return pd.DataFrame()
    out = frames[0]
    for df in frames[1:]:
        out = out.merge(df, on="date", how="outer")
    out.insert(0, "symbol", code)
    out = out.sort_values("date").reset_index(drop=True)
    store.upsert(out, "valuation", code, keys=["symbol", "date"],
                 source="baidu", date_col="date")
    return out


def fetch_index_valuation(
    index_name: str = "沪深300",
    store: ParquetStore | None = None,
) -> pd.DataFrame:
    """
    指数估值历史（乐咕乐股，月度）。
    index_name: 沪深300/上证50/中证500/创业板指 等中文名。
    返回 name,date,index_level,pe_static,pe_ttm,pb_static,pb_ttm。
    """
    store = store or ParquetStore()
    try:
        pe = retry_call(ak.stock_index_pe_lg, symbol=index_name, label="index_pe")
    except Exception:
        return pd.DataFrame()
    pe = pe.rename(columns={
        "日期": "date", "指数": "index_level",
        "静态市盈率": "pe_static", "滚动市盈率": "pe_ttm",
    })
    pe["date"] = pd.to_datetime(pe["date"])
    cols = ["date", "index_level", "pe_static", "pe_ttm"]
    pe = pe[[c for c in cols if c in pe.columns]]

    out = pe
    try:
        pb = retry_call(ak.stock_index_pb_lg, symbol=index_name, label="index_pb")
        pb = pb.rename(columns={
            "日期": "date", "指数": "index_level_pb",
            "静态市净率": "pb_static", "滚动市净率": "pb_ttm",
        })
        pb["date"] = pd.to_datetime(pb["date"])
        pb_cols = [c for c in ["date", "pb_static", "pb_ttm"] if c in pb.columns]
        out = out.merge(pb[pb_cols], on="date", how="left")
    except Exception:
        pass

    out.insert(0, "name", index_name)
    safe = index_name.replace("/", "_")
    store.write(out, "valuation", f"index_{safe}", source="legu", date_col="date")
    return out
