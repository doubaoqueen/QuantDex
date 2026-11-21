"""第 58 步：vipdoc 本地行情库导入器（D-107 发现落地，2026-11-22）。

数据源：国信金太阳（通达信系）盘后数据下载 → D:/MeineTool/goldsun/vipdoc/{sh,sz}/lday/*.day
  每条 32B = date(u32 YYYYMMDD) / open×100 / high×100 / low×100 / close×100 /
  amount(f32 元) / volume(u32 股) / reserved。全市场 9212 只，2012-01-04 起。
  双源验证（D-107）：与我库重叠日 close/volume/amount 逐日精确一致。

**定位红线（D-107）**：.day 无换手率/流通股本——turnover 落 NaN（source=vipdoc），
  本导入器**不替代**日更管线（akshare/baostock），只用于：① 缺行情标的的断网兜底
  补齐；② 未来扩池/全市场扫描的离线批量备料。turnover 依赖型研究（低换手过滤、
  D-105 挖矿）对 vipdoc 来源标的不可用——优先用 57 号（baostock 带换手）。

qfq 换算：qfq = 原始价 × 前复权因子（baostock query_adjust_factor，经
  fetch_adjust_factor 落库 fin_bs/{sym}__adjfactor）；日展开语义与 factors.py
  D-097 同款（ffill + 首事件前 fillna(1.0)）。注意因子抓取 start=2010——
  2010 前有除权事件的标的，其 2012-2017 段换算可能有偏差（validate 模式会暴露；
  本导入器目标区间 2018+ 不受影响）。

用法：python scripts/58_vipdoc_import.py validate [N]   # 抽样验收（默认 8 只）
      python scripts/58_vipdoc_import.py fill           # 补齐 PIT 并集缺行情标的
      python scripts/58_vipdoc_import.py fill 600000 …  # 补指定标的
"""
import struct
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.analysis.experiments import record
from src.data.financials import fetch_adjust_factor
from src.data.storage import ParquetStore

VIPDOC = Path("D:/MeineTool/goldsun/vipdoc")
SCHEMA = ["symbol", "date", "open", "high", "low", "close", "volume", "amount",
          "amplitude", "pct_chg", "change", "turnover", "source"]


def read_day(path: Path) -> pd.DataFrame:
    """通达信 .day → 原始价帧（date/open/high/low/close 元, volume 股, amount 元）。

    amount 字段存的是 float32 位模式（uint32 读入后视图还原，D-107 探针已验证
    与库内 baostock amount 逐日精确一致）。"""
    raw = path.read_bytes()
    n = len(raw) // 32
    recs = struct.unpack(f"<{n * 8}I", raw[:n * 32])
    arr = np.array(recs, dtype=np.uint32).reshape(n, 8)
    return pd.DataFrame({
        "date": pd.to_datetime(arr[:, 0].astype(str), format="%Y%m%d"),
        "open": arr[:, 1] / 100.0, "high": arr[:, 2] / 100.0,
        "low": arr[:, 3] / 100.0, "close": arr[:, 4] / 100.0,
        "amount": arr[:, 5].copy().view(np.float32).astype(np.float64),
        "volume": arr[:, 6].astype(np.int64),
    })


def expand_factor(af: pd.Series | None, dates: pd.DatetimeIndex) -> np.ndarray:
    """事件因子 → 日频（factors.py D-097 同款：ffill + 首事件前 1.0）。"""
    if af is None or len(af) == 0:
        return np.ones(len(dates))
    f = (af.reindex(af.index.union(dates)).sort_index()
         .ffill().reindex(dates).fillna(1.0))
    return f.to_numpy()


def qfq_frame(sym: str, raw: pd.DataFrame, factor: np.ndarray) -> pd.DataFrame:
    """原始价帧 → 库 schema（qfq 价 + 衍生列；turnover=NaN，source=vipdoc）。"""
    df = raw.copy()
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] * factor
    prev = df["close"].shift(1)
    df["pct_chg"] = (df["close"] / prev - 1.0) * 100.0
    df["change"] = df["close"] - prev
    df["amplitude"] = (df["high"] - df["low"]) / prev * 100.0
    df["symbol"] = sym
    df["turnover"] = np.nan
    df["source"] = "vipdoc"
    return df[SCHEMA]


def day_path(sym: str) -> Path | None:
    ex = "sh" if sym.startswith(("6", "9")) else "sz"
    p = VIPDOC / ex / "lday" / f"{ex}{sym}.day"
    return p if p.exists() else None


def factor_series(store: ParquetStore, sym: str, start: str = "2010-01-01") -> pd.Series | None:
    """baostock 因子事件表（fetch 落库后经 store 读，避免重复网络请求）。"""
    try:
        store.read("fin_bs", f"{sym}__adjfactor", columns=["foreAdjustFactor"])
    except Exception:  # noqa: BLE001
        try:
            fetch_adjust_factor(sym, start=start, store=store)
        except Exception as e:  # noqa: BLE001
            print(f"   [warn] {sym} 因子抓取失败：{e}（按无事件 1.0 换算）")
            return None
    try:
        df = store.read("fin_bs", f"{sym}__adjfactor",
                        columns=["dividOperateDate", "foreAdjustFactor"])
    except Exception:  # noqa: BLE001
        return None
    s = pd.Series(pd.to_numeric(df["foreAdjustFactor"], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df["dividOperateDate"], errors="coerce"))
    return s.dropna().sort_index()


def validate(n_sample: int) -> bool:
    """抽样验收：vipdoc×因子换算的 qfq 对照 **fresh baostock 今日真值**。

    对照基准必须是现拉 baostock 而非库内存档——库内日线是三源拼接
    （akshare/sina 主体 + baostock + em 降级行），新浪前复权与 baostock 口径存在
    ~1 tick 级系统差（000002 实测：35/2154 行偏离且全部为 sina 来源，2026-11-22
    三方对照定位）。换算器对 fresh baostock 应零差（volume/amount 与 .day 原始值
    一致性另算，float32 位精度 ~1e-4% 级）。"""
    store = ParquetStore()
    from src.data.sources import bs_query, ensure_bs_login
    bs = ensure_bs_login()
    pit = pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000300.parquet")
    symbols = sorted({str(x) for x in pit["symbol"]})
    sample = [s for s in symbols if store.exists("daily", f"{s}__qfq")
              and day_path(s) is not None][:n_sample]
    print(f">> 验收抽样 {len(sample)} 只（对照 fresh baostock 今日锚）", flush=True)

    worst = []
    for sym in sample:
        code = ("sh" if sym.startswith(("6", "9")) else "sz") + f".{sym}"
        fresh = bs_query(lambda *a, **k: bs.query_history_k_data_plus(*a, **k),
                         code, "date,close,volume,amount",
                         start_date="2018-01-01", end_date="2099-01-01",
                         frequency="d", adjustflag="2")
        fresh["date"] = pd.to_datetime(fresh["date"])
        for c in ("close", "volume", "amount"):
            fresh[c] = pd.to_numeric(fresh[c], errors="coerce")
        raw = read_day(day_path(sym))
        f = expand_factor(factor_series(store, sym), pd.DatetimeIndex(raw["date"]))
        conv = qfq_frame(sym, raw, f)
        j = fresh.merge(conv[["date", "close", "volume", "amount"]], on="date",
                        suffixes=("_b", "_v"))
        if len(j) < 100:
            worst.append((sym, "重叠不足", len(j), np.nan))
            continue
        d_close = (j["close_b"] - j["close_v"]).abs()
        rel_v = ((j["volume_b"] - j["volume_v"]).abs() / j["volume_b"].clip(lower=1)).max()
        rel_a = ((j["amount_b"] - j["amount_v"]).abs() / j["amount_b"].clip(lower=1)).max()
        ok = d_close.max() <= 0.011 and rel_v < 0.001 and rel_a < 0.001
        worst.append((sym, f"{'✓' if ok else '✗'} 重叠 {len(j)} 日", round(d_close.max(), 4),
                      round(max(rel_v, rel_a) * 100, 4)))
        if not ok:
            bad = j[d_close > 0.011]
            print(f"   {sym} 不一致样例（前 3）：\n{bad.head(3).to_string(index=False)}")
    for row in worst:
        print(f"   {row[0]}  {row[1]}  close最大差 {row[2]}  量/额最大相对差 {row[3]}%")
    n_ok = sum(1 for r in worst if str(r[1]).startswith("✓"))
    passed = n_ok == len(worst) and len(worst) > 0
    print(f"\n== 验收 {'PASS' if passed else 'FAIL'}：{n_ok}/{len(worst)} 一致 ==")
    return passed


def fill(symbol_args: list[str]) -> None:
    """补齐缺行情标的（默认 PIT 并集缺失清单；turnover=NaN 见模块红线）。"""
    store = ParquetStore()
    if symbol_args:
        todo = symbol_args
    else:
        p3 = set(pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000300.parquet")["symbol"].astype(str))
        p9 = set(pd.read_parquet(STORE_DIR / "meta" / "pit_constituents_000905.parquet")["symbol"].astype(str))
        todo = [c for c in sorted(p9 - p3) if not store.exists("daily", f"{c}__qfq")]
    print(f">> 待补 {len(todo)} 只（vipdoc 可用 "
          f"{sum(1 for c in todo if day_path(c) is not None)}）", flush=True)
    done = 0
    for sym in todo:
        p = day_path(sym)
        if p is None:
            print(f"   SKIP {sym}（vipdoc 无 .day）")
            continue
        raw = read_day(p)
        f = expand_factor(factor_series(store, sym), pd.DatetimeIndex(raw["date"]))
        store.write(qfq_frame(sym, raw, f), "daily", f"{sym}__qfq",
                    source="vipdoc", date_col="date", adjust="qfq")
        done += 1
        if done % 50 == 0:
            print(f"   {done}/{len(todo)}", flush=True)
    print(f">> 完成 {done} 只写入（source=vipdoc，turnover=NaN）")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "validate"
    if mode == "validate":
        n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 8
        passed = validate(n)
        try:
            record("vipdoc_import_validate", params={"mode": "validate", "n": n},
                   metrics={"passed": int(passed)},
                   notes="vipdoc .day→qfq 换算抽样验收（D-107 落地）")
        except Exception as e:  # noqa: BLE001
            print(f"[experiments] {e}")
        sys.exit(0 if passed else 1)
    elif mode == "fill":
        fill(sys.argv[2:])
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
