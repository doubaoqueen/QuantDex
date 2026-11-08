"""
行情数据：日/周/月线、分钟线、实时快照；支持批量增量更新。
- 个股 / ETF：akshare 东财接口（快、字段全）
- 指数：baostock（东财指数接口分页很慢）
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from config import DEFAULT_START_DATE
from src.data.schema import (
    kline_file_name,
    normalize_code,
    standardize_ak_daily,
    standardize_ak_min,
    standardize_bs_daily,
    standardize_bs_min,
    standardize_sina_daily,
    to_bs_code,
    to_sina_code,
)
from src.data.sources import ak, bs_query, ensure_bs_login, retry_call
from src.data.storage import ParquetStore

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
        # bs_query 内部加锁串行化：baostock 单连接协议，严禁并发调用
        raw = bs_query(
            bs.query_history_k_data_plus,
            bs_code, fields,
            start_date=f"{start[:4]}-{start[4:6]}-{start[6:]}",
            end_date=f"{end[:4]}-{end[4:6]}-{end[6:]}",
            frequency=_BS_FREQ[period], adjustflag=flag,
        )
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


# ---------------------------------------------------------------- 分钟线（baostock）
def fetch_minute_bs(
    symbol: str,
    start: str = "2018-01-01",
    end: str | None = None,
    freq: str = "5",
    store: ParquetStore | None = None,
) -> pd.DataFrame:
    """
    抓取单标的 baostock 分钟线并增量写入仓库（D-060）。

    与东财 fetch_minute（只有滚动约 1-2 个月）不同，baostock 提供
    5/15/30/60 分钟线自 2011 年至今的**全历史**（1 分钟线无免费全历史源）。
    freq: "5"/"15"/"30"/"60"。

    口径：**不复权**（adjustflag=3）——分钟级不做复权锚点，从根上避开日线
    qfq 的增量锚点断裂问题（D-041a）；跨除权日研究可用复权因子后处理。
    baostock 单连接串行协议，调用会经 bs_query 串行化，严禁自行开线程并发。
    """
    store = store or ParquetStore()
    code = normalize_code(symbol)
    end = end or pd.Timestamp.now().strftime("%Y-%m-%d")
    if str(freq) not in ("5", "15", "30", "60"):
        raise ValueError(f"baostock 分钟线 freq 仅支持 5/15/30/60，收到 {freq}")
    bs = ensure_bs_login()
    bs_code = to_bs_code(code)
    fields = "date,time,code,open,high,low,close,volume,amount"
    raw = bs_query(
        bs.query_history_k_data_plus, bs_code, fields,
        start_date=start, end_date=end, frequency=str(freq), adjustflag="3",
    )
    if raw.empty:
        return pd.DataFrame()
    std = standardize_bs_min(raw, code)
    name = f"{code}__{freq}__none"
    store.upsert(std, "minute", name, keys=["symbol", "datetime"],
                 source="baostock/min", date_col="datetime", adjust="none")
    return std


def update_minute_batch(
    symbols: list[str],
    freq: str = "5",
    full_start: str = "20180101",
    store: ParquetStore | None = None,
    backfill_missing: bool = True,
) -> dict:
    """
    【串行】批量增量更新分钟线（baostock 单连接协议，禁止线程池）：
      - 本地最后数据日 >= 最近收盘交易日的标的跳过（周末/盘后重跑秒级）；
      - 其余从本地最后日期起抓（含最后一天，允许同日重抓纠错，与日线 D-019 同语义）；
      - backfill_missing=True（回填模式，29 号脚本）：无本地数据的标的从 full_start
        全史抓取；本地最早日期晚于 full_start 时向前补早段（双向断点续传）；
      - backfill_missing=False（每日调度模式）：无本地数据的标的直接跳过——
        全史抓取约 3 分钟/只（10 万行传输开销），调度窗口内跑不完全池，
        缺失股的回填是 29 号脚本的职责。
    返回 {symbol: 新增行数或"uptodate"/"no-local"}，失败标的收集在 __failed__ 中。
    注册表在 batch_write 内缓冲，全部结束统一写盘一次。
    """
    store = store or ParquetStore()
    report: dict = {"__failed__": []}
    last_closed = last_closed_trade_date(store)
    start_full = f"{full_start[:4]}-{full_start[4:6]}-{full_start[6:]}"
    n = len(symbols)

    def _local_span(name):
        """返回 (最早日期, 最晚日期)（均 normalize 到日）；无数据返回 (None, None)。"""
        if not store.exists("minute", name):
            return None, None
        df = store.read("minute", name, columns=["datetime"])
        if not len(df):
            return None, None
        dt = pd.to_datetime(df["datetime"])
        return dt.min().normalize(), dt.max().normalize()

    consec_fail = {"n": 0}

    def _fetch_retry(sym, start, end=None):
        """单只重试（2/5/10s 退避，第 2 次起强制重连）——服务端断流自愈，D-062。

        连续失败熔断：每累计 10 只全失败，退避 60 秒再继续（服务端限流特征，
        硬冲只会把整个分片烧成失败清单——2026-11-07 上午 5 并发 4.5 小时后被断流的教训）。
        """
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                df = fetch_minute_bs(sym, start=start, end=end, freq=freq, store=store)
                consec_fail["n"] = 0
                return df
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt >= 1:
                    from src.data.sources import force_relogin
                    force_relogin()
                print(f"  [retry] {sym} 第 {attempt + 1} 次失败: {str(e)[:80]}", flush=True)
                time.sleep((2, 5, 10)[min(attempt, 2)])
        consec_fail["n"] += 1
        if consec_fail["n"] % 10 == 0:
            print(f"  [circuit] 连续失败 {consec_fail['n']} 只，退避 60s（疑似服务端限流）",
                  flush=True)
            time.sleep(60)
        raise last_err  # type: ignore[misc]

    with store.batch_write():
        for i, sym in enumerate(symbols, 1):
            code = normalize_code(sym)
            try:
                name = f"{code}__{freq}__none"
                first, last = _local_span(name)
                if first is None:
                    if not backfill_missing:
                        report[code] = "no-local"
                    else:
                        df = _fetch_retry(code, start_full)
                        report[code] = len(df)
                else:
                    if last_closed is None or last < last_closed:
                        df = _fetch_retry(code, last.strftime("%Y-%m-%d"))
                        report[code] = len(df)
                    else:
                        report[code] = "uptodate"
                    if backfill_missing and first > pd.Timestamp(start_full):
                        # 向前补早段：此前从更晚的起始日回填过，现在扩早史（upsert 自动合并）
                        df = _fetch_retry(code, start_full,
                                          end=(first - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
                        if isinstance(report.get(code), int):
                            report[code] += len(df)
            except Exception as e:  # noqa: BLE001  单只失败不拖垮批次
                report["__failed__"].append((code, str(e)))
            if i % 50 == 0 or i == n:
                nf = len(report["__failed__"])
                print(f"  分钟线进度 {i}/{n}，失败 {nf}", flush=True)
            # D-071 节流：符号间微休眠（拉黑教训——礼貌是免费数据源可用性的前提）
            time.sleep(0.3)
    return report


# ---------------------------------------------------------------- qfq 锚点护栏（D-041a/D-062）
def ensure_qfq_integrity(
    symbols: list[str],
    store: ParquetStore | None = None,
    threshold: float = 0.005,
) -> list[str]:
    """行级自洽校验 + 自动修复：|序列涨跌 - pct_chg| > 阈值 视为复权锚点断裂。

    D-041a 上线的护栏原本只在 02b 有、且只检查"本次写入的标的"（isinstance
    筛选还漏掉 bs_fallback/sina_fallback 字符串返回值）——D-062 起下沉为共享
    函数并**全量扫描**：任何写路径（02b 增量、10_daily_run、webapp 快照回填）
    造成的断裂都会在下一次调用时被发现并全史重抓修复。

    返回修复失败的标的列表（空 = 全部干净或修复成功）。
    """
    store = store or ParquetStore()
    broken: list[str] = []
    for code in symbols:
        c = normalize_code(code)
        pth = store._path("daily", f"{c}__qfq")
        if not pth.exists():
            continue
        try:
            k = pd.read_parquet(pth, columns=["close", "pct_chg"])
        except Exception:  # noqa: BLE001  读取失败按破裂处理（重抓覆盖）
            broken.append(c)
            continue
        if len(k) < 2:
            continue
        chg = k["close"] / k["close"].shift(1) - 1
        pct = pd.to_numeric(k["pct_chg"], errors="coerce") / 100
        if ((chg - pct).abs() > threshold).any():
            broken.append(c)
    if not broken:
        return []
    print(f"[guardrail] qfq 自洽破裂 {len(broken)} 只（除权锚点断裂），全史重抓修复",
          flush=True)
    failed: list[str] = []
    for c in broken:
        try:
            fetch_kline(c, start=DEFAULT_START_DATE, asset="stock", adjust="qfq",
                        source="bs", store=store)
        except Exception as e:  # noqa: BLE001
            failed.append(c)
            print(f"[guardrail] {c} 重抓失败: {e}", flush=True)
    print(f"[guardrail] 完成（{len(broken)} 只，失败 {len(failed)}）", flush=True)
    return failed


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


def load_price_matrix(symbols, start: str | None = None, adjust: str = "qfq",
                      store: ParquetStore | None = None):
    """全历史价格矩阵（**不做 PIT 删行**）：回测估值与成交用。

    为什么需要它（D-050）：早先前复权面板先按"当天是不是指数成分"删行，
    非成分期间价格变成 NaN；组合引擎用 ffill 补洞，于是股票重回指数那天
    把几年涨跌压成一天（调样日批量 +300% 的假收益），回测净值被系统性抬高。
    正确做法：估值/收益永远用完整历史，PIT 只用来决定"当天能不能买"。

    返回 (prices, opens)，index=交易日，columns=标的；缺失视为停牌/未上市。
    """
    store = store or ParquetStore()
    suffix = f"__{adjust}"
    file_of = {n.split("__")[0]: n for n in store.list_names("daily") if n.endswith(suffix)}
    frames = []
    for s in symbols:
        name = file_of.get(str(s))
        if name is None:
            continue
        try:
            df = store.read("daily", name, columns=["date", "symbol", "open", "close"])
        except Exception:  # noqa: BLE001
            continue
        if df is None or df.empty:
            continue
        frames.append(df)
    if not frames:
        raise RuntimeError("没有可用日线，先跑 02/02b 更新行情")
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    if start:
        panel = panel[panel["date"] >= pd.Timestamp(start)]
    prices = panel.pivot(index="date", columns="symbol", values="close").sort_index()
    opens = panel.pivot(index="date", columns="symbol", values="open").sort_index()
    return prices, opens


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
    workers: int = 4,
    sina_fallback: bool = False,
    bs_fallback: bool = False,
) -> dict:
    """
    【并发】批量增量更新 K 线：
      - 本地最后日期已 >= 最近收盘交易日的标的直接跳过（不发请求），盘后/周末重跑秒级；
      - 其余标的用线程池并发抓取（IO 密集；默认 4 路，东财对突发并发敏感，
        调大 workers 容易触发限流）；
      - index / source="bs" 的 baostock 查询在 sources.bs_query 内串行化
        （协议单连接，并发会损坏数据流）；
      - 注册表在 batch_write 内缓冲，全部结束统一写盘一次（避免并发写竞争）。
    source: "em"(东财) / "bs"(baostock，稳且覆盖退市股，本机默认，见 D-012)
            / "sina"(新浪，仅个股；依赖 py_mini_racer，本机会段错误，见 D-008)。
    sina_fallback: 东财失败降级新浪（默认 False——py_mini_racer 段错误会连带
      整个进程崩溃，确需时显式传 True）。
    bs_fallback: 东财失败降级 baostock（稳定，推荐开启）。
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
            start = (full_start if last is None
                     else (last + pd.Timedelta(days=1)).strftime("%Y%m%d"))
        except Exception:  # noqa: BLE001  读本地失败也按全新标的处理
            start = full_start
        last_err = None
        for attempt in range(3):
            try:
                if source == "sina":
                    df = fetch_daily_sina(code, start=start, adjust=adjust, store=store)
                else:
                    df = fetch_kline(code, start=start, asset=asset,
                                     period=period, adjust=adjust,
                                     source=source, store=store)
                return code, len(df)
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.8 * (attempt + 1))  # 简单退避，缓解东财限流
        # 个股降级：新浪（会崩，默认关闭）/ baostock（稳，推荐）
        if sina_fallback and source == "em" and asset == "stock":
            try:
                df = fetch_daily_sina(code, start=start, adjust=adjust, store=store)
                return code, f"sina_fallback:{len(df)}"
            except Exception as e:  # noqa: BLE001
                last_err = e
        if bs_fallback and asset == "stock":
            try:
                df = fetch_kline(code, start=start, asset=asset, period=period,
                                 adjust=adjust, source="bs", store=store)
                return code, f"bs_fallback:{len(df)}"
            except Exception as e:  # noqa: BLE001
                last_err = e
        return code, f"__ERR__:{last_err}"

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
