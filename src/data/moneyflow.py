# -*- coding: utf-8 -*-
"""
资金流与情绪数据：
- 北向资金（沪深港通）历史：北向/沪股通/深股通
- 融资融券：沪深两市全历史汇总 + 深交所单日明细
- 龙虎榜明细
金额单位：北向为亿元；两融汇总为万元（数据源原始口径）。
"""
import time
import pandas as pd

from config import REQUEST_INTERVAL
from src.data.storage import ParquetStore
from src.data.sources import ak, retry_call
from src.data.schema import normalize_code


# ---------------------------------------------------------------- 北向资金
def fetch_northbound(channel: str = "北向资金",
                     store: ParquetStore | None = None) -> pd.DataFrame:
    """
    北向资金历史（东财）。channel: 北向资金 / 沪股通 / 深股通。
    返回 date,net_buy,buy_amount,sell_amount,cum_net_buy,holdings_mv（亿元）。
    """
    store = store or ParquetStore()
    df = retry_call(ak.stock_hsgt_hist_em, symbol=channel, label="northbound")
    rename = {
        "日期": "date",
        "当日成交净买额": "net_buy",
        "买入成交额": "buy_amount",
        "卖出成交额": "sell_amount",
        "历史累计净买额": "cum_net_buy",
        "持股市值": "holdings_mv",
    }
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"])
    num_cols = ["net_buy", "buy_amount", "sell_amount", "cum_net_buy", "holdings_mv"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = ["date"] + [c for c in num_cols if c in df.columns]
    df = df[keep].sort_values("date").drop_duplicates("date").reset_index(drop=True)
    name = "northbound" if channel == "北向资金" else f"northbound_{channel}"
    store.upsert(df, "moneyflow", name, keys=["date"], source="akshare", date_col="date")
    return df


# ---------------------------------------------------------------- 融资融券
def fetch_margin_hist(market: str = "sh",
                      store: ParquetStore | None = None) -> pd.DataFrame:
    """
    两市融资融券历史汇总（2010 至今，单位万元）。market: sh / sz。
    """
    store = store or ParquetStore()
    if market == "sh":
        fn = ak.macro_china_market_margin_sh
    elif market == "sz":
        fn = ak.macro_china_market_margin_sz
    else:
        raise ValueError("market 仅支持 sh / sz")
    df = retry_call(fn, label=f"margin_{market}")
    rename = {
        "日期": "date",
        "融资买入额": "rz_buy",
        "融资余额": "rz_balance",
        "融券卖出量": "rq_sell",
        "融券余量": "rq_volume",
        "融券余额": "rq_balance",
        "融资融券余额": "total_balance",
    }
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["rz_buy", "rz_balance", "rq_sell", "rq_volume", "rq_balance", "total_balance"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    store.write(df, "moneyflow", f"margin_{market}", source="akshare", date_col="date")
    return df


def fetch_margin_detail_szse(date: str,
                             store: ParquetStore | None = None) -> pd.DataFrame:
    """深交所某日两融标的明细（date=YYYYMMDD）。"""
    store = store or ParquetStore()
    df = retry_call(ak.stock_margin_szse, date=date, label="margin_detail_szse")
    store.write(df, "moneyflow", f"margin_sz_detail_{date}", source="akshare")
    return df


# ---------------------------------------------------------------- 龙虎榜
def fetch_lhb(start: str, end: str,
              store: ParquetStore | None = None) -> pd.DataFrame:
    """
    龙虎榜明细（东财，单次区间最多返回约 600 条，建议按月抓取）。
    start/end: YYYYMMDD。
    """
    store = store or ParquetStore()
    df = retry_call(ak.stock_lhb_detail_em, start_date=start, end_date=end,
                    label="lhb")
    rename = {
        "代码": "symbol", "名称": "name", "上榜日": "date",
        "龙虎榜净买额": "net_buy", "龙虎榜买入额": "buy_amount",
        "龙虎榜卖出额": "sell_amount", "龙虎榜成交额": "total_amount",
        "换手率": "turnover", "上榜原因": "reason",
    }
    df = df.rename(columns=rename)
    df["symbol"] = df["symbol"].map(normalize_code)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["net_buy", "buy_amount", "sell_amount", "total_amount", "turnover"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = ["symbol", "name", "date", "net_buy", "buy_amount", "sell_amount",
            "total_amount", "turnover", "reason"]
    df = df[[c for c in keep if c in df.columns]]
    df = df.sort_values(["date", "symbol"]).reset_index(drop=True)
    store.upsert(df, "moneyflow", "lhb",
                 keys=["symbol", "date", "reason"], source="akshare", date_col="date")
    time.sleep(REQUEST_INTERVAL)
    return df
