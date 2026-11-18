"""
因子库：从标准化日线构造截面因子面板。

概念
----
- 因子（Factor）= 给每个标的每天算一个「分数」的公式，用来解释/预测未来收益。
- 本文件先在单标的时序上算「原始因子值」，再在每个交易日的横截面上做 rank
  （百分位 0~1），消除量纲与极值。rank 之后：
      * 因子与未来收益做 Pearson 相关 = 业界常用的 RankIC；
      * 按因子 rank 分 5 组即分层回测。
- 因子「值大好还是小好」不预设，统一由检验出的 IC 符号揭示（IC<0 说明越小越好）。

第一版因子全部可由日线（OHLCV + 换手率）计算，无需财报对齐。
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from config import STORE_DIR
from src.data.schema import kline_file_name, normalize_code
from src.data.storage import ParquetStore

# 因子名与中文说明（方向以实测 IC 为准）
FACTOR_NAMES = [
    "mom_20",      # 动量
    "rev_5",       # 短期反转
    "mom_60",      # 中期动量
    "vol_20",      # 低波
    "turnover_20", # 换手/情绪
    "rsi_14",      # 超买超卖
    "ma60_bias",   # 均线偏离
    "amihud_20",   # 非流动性
]

FACTOR_DESC = {
    "mom_20": "过去20日收益率（动量：追涨杀跌）",
    "rev_5": "过去5日收益率（短期反转：跌多了反弹）",
    "mom_60": "过去60日收益率（中期动量）",
    "vol_20": "过去20日日收益标准差（低波因子，理论上越小越好）",
    "turnover_20": "过去20日平均换手率（情绪/投机度，理论上越冷越好）",
    "rsi_14": "14日RSI（>70超买、<30超卖）",
    "ma60_bias": "收盘价相对60日均线的偏离度（乖离，过大易回归）",
    "amihud_20": "Amihud非流动性 |收益|/成交额 的20日均值（越大越缺乏流动性）",
    "dy_ttm": "股息率：过去365日每股分红(除权日已过)/现价（红利风格）",
}


# ---------------------------------------------------------------- 单标的：原始因子
def compute_raw_factors(df: pd.DataFrame) -> pd.DataFrame:
    """在单标的时序上计算原始因子值（未做截面标准化）。"""
    out = df.sort_values("date").copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    amount = pd.to_numeric(out.get("amount"), errors="coerce")
    turnover = pd.to_numeric(out.get("turnover"), errors="coerce")

    out["ret"] = close.pct_change()

    # 动量 / 反转
    out["mom_20"] = close.pct_change(20)
    out["rev_5"] = close.pct_change(5)
    out["mom_60"] = close.pct_change(60)

    # 波动率
    out["vol_20"] = out["ret"].rolling(20).std()

    # 换手率
    out["turnover_20"] = turnover.rolling(20).mean()

    # RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi_14"] = (100 - 100 / (1 + rs)).where(avg_loss != 0, 100)

    # 60 日均线乖离
    ma60 = close.rolling(60).mean()
    out["ma60_bias"] = close / ma60 - 1

    # Amihud 非流动性
    out["amihud_20"] = (
        out["ret"].abs() / amount.replace(0, np.nan)
    ).rolling(20).mean()

    return out


# ---------------------------------------------------------------- 截面因子面板
def st_symbol_set(store: ParquetStore) -> set:
    """当前 ST/*ST 名头股票集合（按**今日**证券简称，实盘口径，D-071）。

    ST 股的流动性枯竭是制度性风险（退市警示+机构禁持），不是因子模型里的
    "冷门安静"——低换手因子会系统性给它们打高分（2026-11-08 候选池 rank4/5
    实测），所以策略选股池默认剔除。名称是当前快照：用于实盘选股无前视；
    用于历史回测是轻度前视（当时未必 ST），量级有限（33/949）。
    """
    try:
        sec = store.read("meta", "bs_securities", columns=["symbol", "name"])
        return {str(s) for s, n in zip(sec["symbol"], sec["name"], strict=False)
                if "ST" in str(n).upper()}
    except Exception:  # noqa: BLE001  证券表缺失时不误杀（宁可放过不可错杀）
        return set()


def build_factor_panel(
    store: ParquetStore,
    symbols: list[str],
    start: str | None = None,
    factor_names: list[str] | None = None,
    adjust: str = "qfq",
    pit_constituents: pd.DataFrame | None = None,
    dividend_panel: pd.DataFrame | None = None,
    expr_factors: dict[str, str] | None = None,
    exclude_st: bool = False,
) -> pd.DataFrame:
    """
    读取多标的日线 -> 算原始因子 -> 拼成长表 -> 每个交易日截面 rank(pct)。

    返回列：date, symbol, close, ret, 以及各因子的截面百分位(0~1)。
    adjust 指定读取哪套复权口径（默认 qfq，与 04 脚本写入口径一致）。

    pit_constituents: 传入 point-in-time 成分表（universe.load_pit_constituents 的输出）
    时，面板只保留「当天确实是该指数成分」的 (date, symbol) 行——消除幸存者偏差。
    判定口径：in_date <= date < out_date，out_date 为空视为至今仍是成分。

    dividend_panel: 传入分红面板（financials.load_dividend_panel 输出：
    date=除权日, symbol, dividend=每股税前红利）时，追加计算股息率因子 dy_ttm
    （过去 365 日已除权分红 / 现价；除权日未到的分红不进窗口，无前视）。

    expr_factors: 传入表达式因子字典（{因子名: 表达式}，见 expr_factors.EXPR_FACTORS）
    时，按表达式引擎逐标的计算并一并截面 rank——表达式只用 Ref(x, n>0)，无前视。
    """
    factor_names = factor_names or FACTOR_NAMES
    extra = ["dy_ttm"] if dividend_panel is not None else []
    if expr_factors:
        extra = [*extra, *expr_factors.keys()]
    keep = ["date", "symbol", "open", "close", "ret", *factor_names, *extra]
    div_by_sym: dict = {}
    if dividend_panel is not None:
        d = dividend_panel.copy()
        d["date"] = pd.to_datetime(d["date"])
        div_by_sym = {str(k): g for k, g in d.groupby("symbol")}
    frames = []

    _EXPR_FIELDS = ("open", "high", "low", "close", "volume", "amount", "turnover")

    for sym in symbols:
        code = normalize_code(sym)
        name = kline_file_name(code, adjust)
        if not store.exists("daily", name):
            continue
        # 列裁剪：因子只需要日期/代码/收盘/成交额/换手率，Parquet 列存只读这几列
        df = store.read("daily", name,
                        columns=["date", "symbol", "open", "close", "amount", "turnover"])
        raw = compute_raw_factors(df)
        if code in div_by_sym:
            g = div_by_sym[code]
            s = g.groupby("date")["dividend"].sum().sort_index()
            daily = s.reindex(pd.DatetimeIndex(raw["date"])).fillna(0.0)
            ttm = daily.rolling("365D").sum()
            raw["dy_ttm"] = ttm.to_numpy() / raw["close"].to_numpy()
        elif extra and "dy_ttm" in extra:
            raw["dy_ttm"] = np.nan
        if expr_factors:
            from src.analysis.expr import evaluate
            # 表达式需要 OHLCV 全字段：补读（列裁剪只对基础读取生效）
            full = store.read("daily", name,
                              columns=list(_EXPR_FIELDS))
            fields = {c: full[c] for c in _EXPR_FIELDS if c in full.columns}
            for fname, expr in expr_factors.items():
                try:
                    v = evaluate(expr, fields)
                    raw[fname] = v.to_numpy()
                except Exception as e:  # noqa: BLE001
                    raw[fname] = np.nan
                    print(f"[expr] {code} 因子 {fname} 计算失败: {e}")
        frames.append(raw[keep])

    if not frames:
        raise RuntimeError("没有可用日线数据，请先抓取行情。")

    panel = pd.concat(frames, ignore_index=True)
    if start:
        panel = panel[panel["date"] >= pd.Timestamp(start)]

    # point-in-time 过滤：只保留当时真实在指数内的 (date, symbol)
    if pit_constituents is not None:
        pit = pit_constituents[["symbol", "in_date", "out_date"]].copy()
        pit["in_date"] = pd.to_datetime(pit["in_date"])
        pit["out_date"] = pd.to_datetime(pit["out_date"])
        merged = panel.merge(pit, on="symbol", how="inner")
        keep_rows = (
            (merged["date"] >= merged["in_date"])
            & (merged["out_date"].isna() | (merged["date"] < merged["out_date"]))
        )
        panel = merged[keep_rows].drop(columns=["in_date", "out_date"])
        panel = panel.reset_index(drop=True)

    # ST 剔除必须在截面 rank 之前：ST 的极端低换手会污染全池分位数（D-071）
    if exclude_st:
        st = st_symbol_set(store)
        if st:
            panel = panel[~panel["symbol"].isin(st)].reset_index(drop=True)

    # 截面 rank：同一天所有股票横向排名为百分位（一次 groupby 完成全部因子）
    rank_cols = [*factor_names, *extra]
    panel[rank_cols] = panel.groupby("date")[rank_cols].rank(pct=True)

    panel = panel.sort_values(["date", "symbol"]).reset_index(drop=True)
    return panel



# ---------------------------------------------------------------- 全池因子快照（D-084）

SNAPSHOT_PATH = STORE_DIR / "cache" / "factor_snapshot.parquet"   # 日更预计算缓存（测试可替换）


def factor_last_row_wide(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """纯计算核心：每标的一个日线帧（须含 date/symbol/close/amount/turnover/pct_chg 列），
    一次算出全部标的的最新因子行（宽面板向量化）。

    为什么存在（D-084，2026-11-17）：candidates 原实现逐标的循环（每只读盘 1.9ms +
    pandas 因子 3.6ms，949 只 ≈5~10s），瓶颈在调用开销不在算力——同样的因子宽面板
    一次算纯数学只要 0.19s（实测）。向量化后全池 ≈2~3s，配合日更预计算缓存
    （get_factor_snapshot）后 Web 交互延迟与股票池规模解耦（全 A/港美扩展不再恶化）。

    与逐只口径（compute_raw_factors）的等价性规则：
    ① 各标的取**自己最后有效行**上的因子值（等价于旧口径"用自己最后一行"）；
    ② **新鲜度约束**——因子最后有效值的行位次必须与 close 一致：停牌占位/脏行型
    （close 冻结但 turnover 全 NaN，如 000016 停牌两月）因子置 NaN、被调用方剔除
    （与旧口径三因子 isfinite 过滤一致）；纯缺行型停牌（文件里没有停牌期行）不受
    影响（旧口径同样保留）。有 ≥70 行的标的才入快照（与旧 len(k)>=70 一致）。
    等价性核验见 tests/test_factor_snapshot.py（含真实库全量对照）。

    返回 DataFrame（index.name="symbol"）：close/pct_chg(小数)/8 因子列；
    attrs["as_of"]=宽矩阵最后交易日，attrs["last_dates"]=各标的自己的最后交易日
    （供分红 TTM 窗口用，停牌标的窗口终点=它自己的最后行，与旧逐只口径一致）。
    """
    kept: list[pd.DataFrame] = []
    for df in frames:
        d = df[["date", "symbol", "close", "amount", "turnover", "pct_chg"]].copy()
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        for c in ("close", "amount", "turnover", "pct_chg"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d.dropna(subset=["date"])
        kept.append(d)
    if not kept:
        return pd.DataFrame()
    panel = pd.concat(kept, ignore_index=True)
    if panel.empty:
        return pd.DataFrame()

    # 宽矩阵（date × symbol）：factorize 建索引后一次性填充，比逐列 pivot 快一个量级
    dates = pd.DatetimeIndex(np.sort(panel["date"].unique()))
    sym_idx, sym_codes = pd.factorize(panel["symbol"])   # codes=行→列位次, uniques=列标签
    row_idx = dates.searchsorted(panel["date"].to_numpy())

    def _mat(col: str) -> pd.DataFrame:
        m = np.full((len(dates), len(sym_codes)), np.nan)
        m[row_idx, sym_idx] = panel[col].to_numpy()
        return pd.DataFrame(m, index=dates, columns=sym_codes)

    close = _mat("close")
    amt = _mat("amount")
    turn = _mat("turnover")
    pct = _mat("pct_chg")

    def _last_vals(dfm: pd.DataFrame):
        """各列（标的）从末尾数的最后有效值 + 其行位次（整列 NaN → 位次 -1、值 NaN）。"""
        v = dfm.to_numpy(dtype=float)
        valid = ~np.isnan(v)
        anyv = valid.any(axis=0)
        pos = np.where(anyv, v.shape[0] - 1 - np.argmax(valid[::-1], axis=0), -1)
        vals = np.where(anyv, v[np.clip(pos, 0, v.shape[0] - 1), np.arange(v.shape[1])], np.nan)
        return pd.Series(vals, index=dfm.columns), pos

    # 新鲜度约束（2026-11-17 验收抓到的停牌标的缺陷）：因子最后有效值的**行位次**
    # 必须与 close 一致——即值落在标的自己的最后有效行上。ffill 只允许跨"缺行"
    # （停牌缺行型，旧口径同样保留），不允许跳过"行内坏行"（停牌占位/脏行型，
    # 000016 停牌两月 close 冻结但 turnover 全 NaN——旧口径剔除，ffill 会错误地
    # 用停牌前的旧因子值把它救活进候选池）。
    close_vals, close_pos = _last_vals(close)

    # 因子公式与 compute_raw_factors 逐字对应（滚动沿时间轴滑动，逐列独立）
    ret = close.pct_change()
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)

    snap = pd.DataFrame({"close": close_vals})
    for name, fm in (
        ("pct_chg", pct),
        ("mom_20", close.pct_change(20)),
        ("rev_5", close.pct_change(5)),
        ("mom_60", close.pct_change(60)),
        ("vol_20", ret.rolling(20).std()),
        ("turnover_20", turn.rolling(20).mean()),
        ("rsi_14", (100 - 100 / (1 + rs)).where(avg_loss != 0, 100)),
        ("ma60_bias", close / close.rolling(60).mean() - 1),
        ("amihud_20", (ret.abs() / amt.replace(0, np.nan)).rolling(20).mean()),
    ):
        vals, pos = _last_vals(fm)
        fresh = np.where(pos == close_pos, vals, np.nan)
        snap[name] = fresh / 100.0 if name == "pct_chg" else fresh   # 仓库百分数 → 小数
    snap.attrs["as_of"] = dates[-1]
    snap.attrs["last_dates"] = close.apply(pd.Series.last_valid_index)   # symbol → 自己的最后交易日
    # ≥70 行才入快照（旧 candidates 口径；年幼上市标的因子语义不足）
    n_rows = close.notna().sum()
    snap = snap.loc[n_rows[n_rows >= 70].index]
    snap.index.name = "symbol"
    return snap


def factor_snapshot_wide(symbols, store: ParquetStore | None = None,
                         include_dy: bool = True) -> pd.DataFrame:
    """全池因子快照（读仓库 → 宽面板向量化）：factor_last_row_wide 的仓库入口。

    include_dy=True 时追加 dy_ttm（各标的近 365 日每股分红之和 / 现价，TTM 口径）；
    无分红标的该列为 NaN（红利账户按 >0 过滤，与旧 candidates 一致）。
    """
    store = store or ParquetStore()
    frames = []
    for s in symbols:
        try:
            df = store.read("daily", kline_file_name(str(s), "qfq"),
                            columns=["date", "close", "amount", "turnover", "pct_chg"])
        except Exception:  # noqa: BLE001  缺文件/坏文件跳过（与旧循环 continue 同口径）
            continue
        if df is None or df.empty:
            continue
        df["symbol"] = str(s)
        frames.append(df)
    snap = factor_last_row_wide(frames)
    snap["dy_ttm"] = np.nan
    if snap.empty or not include_dy:
        return snap
    from src.data.financials import load_dividend_panel
    try:
        dpanel = load_dividend_panel(store, symbols=[str(s) for s in symbols])
    except Exception:  # noqa: BLE001  分红缺失不拖垮快照（红利账户按 NaN 过滤即可）
        dpanel = pd.DataFrame()
    if dpanel.empty:
        return snap
    last_dates = snap.attrs.get("last_dates")
    d = dpanel.dropna(subset=["date", "dividend"]).copy()
    d["symbol"] = d["symbol"].astype(str)
    for sym, g in d.groupby("symbol"):
        end = last_dates.get(sym) if last_dates is not None else None
        if end is None or pd.isna(end):
            continue
        # TTM = 该标的最后交易日往前 365 个日历日内的分红和（旧逐只口径等价）
        win = g[(g["date"] > end - pd.Timedelta(days=365)) & (g["date"] <= end)]
        ttm = float(win["dividend"].sum())
        if ttm > 0:
            snap.loc[sym, "dy_ttm"] = ttm / snap.at[sym, "close"]
    return snap


def _save_snapshot_cache(snap: pd.DataFrame, as_of) -> None:
    """快照落盘缓存（附 as_of 常量列——parquet 不保留 attrs，落盘只认列）。

    D-085：改为临时文件 + os.replace 原子替换。此前直写目标文件，而 Web 面板与
    `10_daily_run` 的预计算是两个进程——流水线写缓存的瞬间，面板正好读到一个
    "能解析但行数不全"的半截文件，于是候选池缺股票、策略动向 500（2026-11-17 19:59
    实测：用户点「跑流水线」后策略动向报 HTTP 500）。
    Windows 上目标文件被读者占用时 os.replace 会失败，退回直写（此时那一次读已经
    结束，概率极低；真正的护栏在 webapp 侧的空面板/缺标的兜底）。
    """
    try:
        if as_of is None or snap.empty:
            return
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = snap.reset_index()
        out["as_of"] = pd.Timestamp(as_of)
        out.attrs = {}   # pandas 3 会把 attrs JSON 序列化进 parquet 元数据，Timestamp/Series 直接炸
        tmp = SNAPSHOT_PATH.with_name(SNAPSHOT_PATH.name + ".tmp")
        try:
            out.to_parquet(tmp, index=False)
            os.replace(tmp, SNAPSHOT_PATH)
        except OSError:                     # Windows：目标被读者打开 → 退回直写
            out.to_parquet(SNAPSHOT_PATH, index=False)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:  # noqa: BLE001  缓存失败不影响主路径（下次现算）
        pass


def _load_snapshot_cache(latest) -> pd.DataFrame | None:
    """读缓存：as_of == 最新交易日才有效（PIT 并集与行情同一日更链更新，日内不漂移；
    盘中单独补成分等罕见情形下一晚自愈，或调用方 force=True）。"""
    if latest is None:
        return None
    try:
        cached = pd.read_parquet(SNAPSHOT_PATH)
        if pd.to_datetime(cached["as_of"].iloc[0]) != pd.Timestamp(latest):
            return None
        cached = cached.drop(columns=["as_of"])
        cached["symbol"] = cached["symbol"].astype(str)
        snap = cached.set_index("symbol")
        snap.attrs["as_of"] = pd.Timestamp(latest)
        return snap
    except Exception:  # noqa: BLE001  缺文件/坏文件 → 当无缓存
        return None


def get_factor_snapshot(symbols, store: ParquetStore | None = None,
                        force: bool = False, latest=None) -> pd.DataFrame:
    """带缓存的快照入口（Web 与日更预计算共用）。

    latest=None 时从 000300 指数日线解析最新交易日（与 candidates 展示日期同源）；
    测试可直接传 latest 注入。缓存命中 <0.1s；未命中向量化现算 ≈3s 并落盘。
    """
    store = store or ParquetStore()
    if latest is None:
        try:
            k0 = store.read("daily", kline_file_name("000300", "none"), columns=["date"])
            latest = pd.to_datetime(k0["date"]).max()
        except Exception:  # noqa: BLE001
            latest = None
    if not force:
        cached = _load_snapshot_cache(latest)
        if cached is not None:
            return cached
    snap = factor_snapshot_wide(symbols, store=store)
    if latest is not None:
        snap.attrs["as_of"] = pd.Timestamp(latest)
    _save_snapshot_cache(snap, latest)
    return snap
