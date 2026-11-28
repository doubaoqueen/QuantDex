"""
标的池与基础数据：股票/ETF/指数/可转债列表、交易日历、全量证券、指数成分股。
所有结果落 Parquet 仓库的 meta 分区。
"""
import time

import pandas as pd

from config import REQUEST_INTERVAL
from src.data.schema import normalize_code, to_bs_code
from src.data.sources import ak, bs_query, bs_to_df, ensure_bs_login, retry_call
from src.data.storage import ParquetStore


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


def fetch_industry_sw(store: ParquetStore | None = None) -> pd.DataFrame:
    """申万一级行业成分（akshare）：31 个行业逐个拉成分，拼成 symbol→行业映射。

    申万是 A 股量化的标准行业分类（优于东财板块）；东财板块接口对本机连接不稳
    （RemoteDisconnected，同 D-012 的东财问题），故选申万。
    成分表带「计入日期」，可作行业归属的近似时间线。
    限制：只覆盖**当前上市**股票——退市股无行业，PIT 回测做行业中性化时
    需容忍 NaN 行业（丢弃或归入"未知"）。
    返回 symbol, name, industry, industry_code, in_date，落盘 meta/industry_sw。
    """
    store = store or ParquetStore()
    info = retry_call(ak.sw_index_first_info, label="sw_first_info")
    frames = []
    n = len(info)
    for i, row in enumerate(info.itertuples(index=False), 1):
        code = str(getattr(row, "行业代码", row[0])).strip().split(".")[0]  # 801010.SI -> 801010
        iname = str(getattr(row, "行业名称", row[1])).strip()
        try:
            cons = retry_call(ak.index_component_sw, symbol=code,
                              label=f"sw_cons:{code}")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] 行业「{iname}」获取失败: {str(e)[:60]}", flush=True)
            continue
        d = cons.rename(columns={"证券代码": "symbol", "证券名称": "name",
                                 "计入日期": "in_date"})
        d["symbol"] = d["symbol"].map(normalize_code)
        d["industry"] = iname
        d["industry_code"] = code
        keep = [c for c in ("symbol", "name", "industry", "industry_code", "in_date")
                if c in d.columns]
        frames.append(d[keep])
        time.sleep(REQUEST_INTERVAL)
        if i % 10 == 0 or i == n:
            print(f"  申万一级行业 {i}/{n}", flush=True)
    if not frames:
        raise RuntimeError("申万行业成分全部获取失败")
    df = (pd.concat(frames, ignore_index=True)
            .drop_duplicates("symbol").sort_values("symbol").reset_index(drop=True))
    store.write(df, "meta", "industry_sw", source="akshare/sw")
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


# ---------------------------------------------------------------- point-in-time 成分
def load_pit_constituents(index_code: str = "000300",
                          store: ParquetStore | None = None) -> pd.DataFrame:
    """读取 point-in-time 历史成分表（消除幸存者偏差的基础数据）。

    每行一只股票的「进出区间」：symbol, name, in_date(纳入生效日), out_date(剔除生效日)。
    判定某日 t 是否为成分：in_date <= t < out_date（out_date 为 NaT 表示至今仍为成分）。

    数据源：github unliftedq/index-constitution（中证指数公司官方调整记录整理，
    与 csindex 官网当前成分交叉验证 300/300 一致）。
    覆盖：沪深300(000300) 自 2005-04-08、中证500(000905) 自 2007-01-15。
    """
    store = store or ParquetStore()
    name = f"pit_constituents_{normalize_code(index_code)}"
    if not store.exists("meta", name):
        raise FileNotFoundError(
            f"PIT 成分表不存在: meta/{name}。请先运行 scripts/00_fetch_pit_constituents.py")
    return store.read("meta", name)


def pit_union_symbols(index_code: str = "000300",
                      store: ParquetStore | None = None) -> list[str]:
    """该指数历史成分并集（曾进入过的全部股票，含已剔除/退市）。"""
    pit = load_pit_constituents(index_code, store)
    return sorted(pit["symbol"].unique().tolist())


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


# ---------------------------------------------------------------- 按日 ST 标记（时点数据，D-118）
ST_FLAG_NAME = "st_flag"            # meta/st_flag.parquet：symbol, date, is_st
ST_FLAG_START = "2015-01-01"        # 因子最长回看 365 日，2018 起的回测留足预热
ST_FLAG_CHECKPOINT = 50             # 每抓满多少只落一次盘（断点续抓的粒度）
_BS_FLAG_NONE = "3"                 # 不复权：isST 与价格无关，只是随行字段


def fetch_st_flag(symbol: str, start: str = ST_FLAG_START,
                  end: str | None = None) -> pd.DataFrame:
    """抓取单只股票的逐日 ST 标记（baostock 日线随行字段 isST，1=当日 ST/*ST）。

    start/end 为 ISO 日期（YYYY-MM-DD，baostock 接口口径）；end 缺省到今天。

    为什么要有这张表（D-118）：`exclude_st` 早先按**今天的简称**剔除全部历史，
    把尚未戴帽时期的持仓一并抹掉——2026-11-26 审计实测，策略持有过的 7 只现 ST 股
    33 次持有全部早于戴帽日（苏宁易购 2022-05-06、中南建设 2024-04-24、阳光城
    2023-05-05……）。isST 在当天就成立，是真正的时点数据，回测与检验据此只剔除
    **当日确实处于 ST** 的股票日。
    """
    code = normalize_code(symbol)
    bs = ensure_bs_login()
    raw = bs_query(bs.query_history_k_data_plus, to_bs_code(code), "date,isST",
                   start_date=start, end_date=end or pd.Timestamp.now().strftime("%Y-%m-%d"),
                   frequency="d", adjustflag=_BS_FLAG_NONE)
    if raw.empty:
        return pd.DataFrame({"symbol": pd.Series(dtype=str),
                             "date": pd.Series(dtype="datetime64[ns]"),
                             "is_st": pd.Series(dtype="int8")})
    out = pd.DataFrame({
        "symbol": code,
        "date": pd.to_datetime(raw["date"], errors="coerce"),
        "is_st": pd.to_numeric(raw["isST"], errors="coerce").fillna(0).astype("int8"),
    })
    return (out.dropna(subset=["date"]).drop_duplicates("date")
            .sort_values("date").reset_index(drop=True))


def update_st_flag(symbols, store: ParquetStore | None = None,
                   start: str = ST_FLAG_START, sleep: float = REQUEST_INTERVAL,
                   checkpoint: int = ST_FLAG_CHECKPOINT, log=print) -> dict:
    """增量更新按日 ST 标记表（断点续抓：已有记录的标的只抓最后日期之后）。

    一只标的一次请求；每满 checkpoint 只原子覆盖整表一次，中断后重跑即续抓。
    返回 {"updated", "uptodate", "empty", "failed", "rows"}。
    """
    store = store or ParquetStore()
    old = store.read_or_empty("meta", ST_FLAG_NAME)
    last = (old.groupby("symbol")["date"].max() if len(old)
            else pd.Series(dtype="datetime64[ns]"))
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    stats = {"updated": 0, "uptodate": 0, "empty": 0, "failed": [], "rows": int(len(old))}
    fetched: list[pd.DataFrame] = []        # 本次运行抓到的全部帧（落盘时整体重建，避免批次互相覆盖）

    def _flush() -> None:
        if not fetched:
            return
        new = pd.concat(fetched, ignore_index=True)
        merged = (new if old.empty
                  else pd.concat([old[~old["symbol"].isin(new["symbol"])], new], ignore_index=True))
        merged = merged.sort_values(["symbol", "date"]).reset_index(drop=True)
        store.write_atomic(merged, "meta", ST_FLAG_NAME, source="baostock/isST", date_col="date")
        stats["rows"] = int(len(merged))

    for i, sym in enumerate(symbols, 1):
        code = normalize_code(str(sym))
        try:
            since = ((pd.Timestamp(last[code]) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                     if code in last.index else start)
            if since > today:
                stats["uptodate"] += 1
            else:
                df = fetch_st_flag(code, start=since)
                if df.empty:
                    stats["empty"] += 1
                else:
                    fetched.append(df)
                    stats["updated"] += 1
                time.sleep(sleep)        # 符号间微休眠（拉黑教训，同 D-071）
        except Exception as e:  # noqa: BLE001  单只失败不拖垮批次，重跑续抓
            stats["failed"].append((code, str(e)[:80]))
        if stats["updated"] and stats["updated"] % checkpoint == 0 and i < len(symbols):
            _flush()
            log(f"   进度 {i}/{len(symbols)} | 已抓 {stats['updated']} | 表内 {stats['rows']} 行")
    _flush()
    return stats
