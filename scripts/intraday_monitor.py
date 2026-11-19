"""日内盯盘监控器（D-089，用户 2026-11-18"机器替我盯盘"的一键原型）。

身份与边界（纪律红线）：
  只读快照、只写日志、**绝不写账本、绝不自动下单**——触发 = 记录 + 提醒，
  不是信号（D-088 已证明触发规则无统计优势）；它的价值是"留痕的盯盘"：
  表弟盯一年剩模糊印象，它盯一天每根羊毛都落盘。

触发规则（预注册，启动时写进日志，禁止盘中改）：
  R1 动量：涨幅 ≥ 5%（主板）/ ≥ 8%（创业板 30/科创 68）
  R2 近板：涨幅 ≥ 7.8%（主板）/ ≥ 17.8%（创业科创）——距涨停 ≤ 2 个百分点
  R3 放量启动：量比 ≥ 3 且涨幅 ≥ 3%
  限频：每标的每规则冷却 45 分钟（R2 冷却 90 分钟），杜绝重复刷屏。
  监听池：沪深 A 股（60/00/30/68 开头），剔除 ST。

数据留痕（研究资产，D-087 缺的变量从这里开始积累）：
  data/store/logs/monitor/monitor_YYYYMMDD.log   全过程（含报错栈）
  data/store/logs/monitor/triggers_YYYYMMDD.csv  逐触发事件
  data/store/logs/monitor/polls_YYYYMMDD.parquet 全量快照时序（收盘落盘）

用法（一键）：scripts/run_monitor.bat  → 早晨任意时间双击，自动等到 09:20 开工，
  15:05 收工写总结。收盘后别忘了金太阳客户端点一次"盘后数据下载"。
"""
import io
import json
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401
import numpy as np
import pandas as pd

from config import STORE_DIR
from src.data.snapshot import fetch_spot

LOG_DIR = STORE_DIR / "logs" / "monitor"
POLL_SLEEP_S = 20            # 轮询间隔（东财全市场一拉 ~70s，实测周期 ≈ 90s）
SESSION_START = "09:20"
SESSION_END = "15:05"
LUNCH = (pd.Timestamp("11:31").time(), pd.Timestamp("12:59").time())
COOLDOWN = {"R1": 45, "R2": 90, "R3": 45}
MAX_CONSEC_FAIL = 15         # 连续失败 15 次 → 大退避（不退出，数据资产要紧）
MAX_POLLS = 400              # 硬上限防失控


def _rules_for(code: str) -> dict:
    """分板阈值：创业板(30)/科创(68) 20%，主板 10%。"""
    gem = code.startswith(("30", "68"))
    return {"r1": 8.0 if gem else 5.0, "r2": 17.8 if gem else 7.8}


def _watchable(df: pd.DataFrame) -> pd.DataFrame:
    """监听池：沪深 A 股（60/00/30/68），剔 ST，剔北交所。"""
    df = df[df["代码"].astype(str).str.startswith(("60", "00", "30", "68"))]
    return df[~df["名称"].astype(str).str.upper().str.contains("ST")].copy()


def _trigger_checks(watch: pd.DataFrame, state: dict, now_ts) -> pd.DataFrame:
    """三条预注册规则 + 限频（向量化：itertuples 会把中文列名 mangle 成 _N）。"""
    pct = pd.to_numeric(watch["涨跌幅"], errors="coerce")
    vr = pd.to_numeric(watch["量比"], errors="coerce")
    gem = watch["代码"].astype(str).str.startswith(("30", "68"))
    th_r1 = pd.Series(np.where(gem, 8.0, 5.0), index=watch.index)
    th_r2 = pd.Series(np.where(gem, 17.8, 7.8), index=watch.index)
    m1, m2 = pct >= th_r1, pct >= th_r2
    m3 = (vr >= 3.0) & (pct >= 3.0)
    hits = []
    for idx in watch.index[m1 | m2 | m3]:
        code = str(watch.at[idx, "代码"])
        p_, v_ = float(pct.loc[idx]), float(vr.loc[idx]) if pd.notna(vr.loc[idx]) else np.nan
        rules = []
        if p_ >= th_r1.loc[idx]:
            rules.append("R1")
        if p_ >= th_r2.loc[idx]:
            rules.append("R2")
        if pd.notna(v_) and v_ >= 3.0 and p_ >= 3.0:
            rules.append("R3")
        for rule in rules:
            last = state.get((code, rule))
            if last is not None and (now_ts - last).total_seconds() < COOLDOWN[rule] * 60:
                continue
            state[(code, rule)] = now_ts
            hits.append({"time": now_ts, "rule": rule, "code": code,
                         "name": watch.at[idx, "名称"],
                         "price": watch.at[idx, "最新价"], "pct_chg": p_, "量比": v_,
                         "涨速": watch.at[idx, "涨速"], "5分钟涨跌": watch.at[idx, "5分钟涨跌"]})
    return pd.DataFrame(hits)


def _entry_moments(watch: pd.DataFrame, m_state: dict, now_ts) -> pd.DataFrame:
    """章法定义的介入时刻（预注册，每标的每日一次；出场参考最多三次）。

    M1 追涨介入：日内涨幅首次越过 3%，且尚未进入近板区（D-088 硬币口径）
    M2 近板介入：涨幅首次越过 7.8%/17.8%，但价格未封死（可买近似，D-093 口径）
    M3 出场参考：介入后从日内峰值回撤 ≥2pp（"达到预期就卖"的反面=止损时机）
    纪律：这些是**章法时刻的记录**，不是系统推荐（D-088 已证无统计优势）——
    记录 + 次日自动回填 T+1 结果，让未来的数据裁决章法。
    """
    pct = pd.to_numeric(watch["涨跌幅"], errors="coerce")
    hits = []
    for idx in watch.index:
        code = str(watch.at[idx, "代码"])
        st = m_state.setdefault(code, {"m1": False, "m2": False, "peak": -99.0, "m3_n": 0})
        p_ = pct.loc[idx]
        if pd.isna(p_):
            continue
        price = watch.at[idx, "最新价"]
        prev_close = pd.to_numeric(watch.at[idx, "昨收"], errors="coerce")
        gem = code.startswith(("30", "68"))
        lim_r = 0.195 if gem else 0.095
        sealed = (pd.notna(prev_close) and pd.notna(price)
                  and price >= round(prev_close * (1 + lim_r), 2) - 0.005)
        near_board = 17.8 if gem else 7.8
        st["peak"] = max(st["peak"], float(p_))
        if sealed:
            continue                                   # 封板买不进，介入时刻不成立
        if (not st["m1"] and not st["m2"] and float(p_) >= 3.0
                and float(p_) < near_board):
            # 曾进近板区（m2）后回落不算追涨介入——那是回撤，属 M3 管辖
            st["m1"] = True
            hits.append({"time": now_ts, "kind": "M1追涨介入", "code": code,
                         "name": watch.at[idx, "名称"], "price": price,
                         "pct_chg": float(p_), "量比": watch.at[idx, "量比"],
                         "日内峰值%": st["peak"], "备注": "涨幅首越3%且距板≥2pp"})
        if not st["m2"] and float(p_) >= near_board:
            st["m2"] = True
            hits.append({"time": now_ts, "kind": "M2近板介入", "code": code,
                         "name": watch.at[idx, "名称"], "price": price,
                         "pct_chg": float(p_), "量比": watch.at[idx, "量比"],
                         "日内峰值%": st["peak"], "备注": "首次进入距板2pp区（未封死）"})
        if ((st["m1"] or st["m2"]) and st["m3_n"] < 3
                and st["peak"] - float(p_) >= 2.0):
            st["m3_n"] += 1
            hits.append({"time": now_ts, "kind": "M3出场参考", "code": code,
                         "name": watch.at[idx, "名称"], "price": price,
                         "pct_chg": float(p_), "量比": watch.at[idx, "量比"],
                         "日内峰值%": st["peak"],
                         "备注": f"较日内峰值回撤 {st['peak'] - float(p_):.1f}pp"})
    return pd.DataFrame(hits)


def _backfill_moments(mom_path: Path, store, log) -> None:
    """启动时回填昨日介入时刻的 T+1 结果（日更链 19:00 已入库，此刻可得）。"""
    if not mom_path.exists():
        return
    try:
        m = pd.read_csv(mom_path, dtype={"code": str})
        if "t1_open_ret" in m.columns and m["t1_open_ret"].notna().any():
            return                                     # 已回填过
        from src.data.market import get_kline
        opens, closes = [], []
        for _, r in m.iterrows():
            o, c = np.nan, np.nan
            try:
                k = get_kline(str(r["code"]), adjust="qfq", store=store)
                k = k.reset_index(drop=True)
                d = pd.to_datetime(r["time"]).normalize()
                pos = k.index[k["date"] == d]
                if len(pos) and pos[0] + 1 < len(k):
                    nxt = k.iloc[pos[0] + 1]
                    o = nxt["open"] / r["price"] - 1.0
                    c = nxt["close"] / r["price"] - 1.0
            except Exception:  # noqa: BLE001
                pass
            opens.append(o)
            closes.append(c)
        m["t1_open_ret"] = opens
        m["t1_close_ret"] = closes
        m.to_csv(mom_path, index=False, encoding="utf-8-sig")
        ok = m["t1_open_ret"].notna().sum()
        mean_o = m["t1_open_ret"].mean()
        mean_c = m["t1_close_ret"].mean()
        log.write(f">> [回填] 昨日介入时刻 {len(m)} 条，补 T+1 结果 {ok} 条"
                  f"（均值 {mean_o:+.2%} 开盘 / {mean_c:+.2%} 收盘）\n")
        log.flush()
    except Exception as e:  # noqa: BLE001
        log.write(f">> [回填] 失败（不影响监控）：{e}\n")
        log.flush()


def _run_tdx_probe(log) -> None:
    """启动时顺跑 TDX 探针（结果进日志；通不通都不影响监控本体）。"""
    log.write(">> [TDX探针] 金太阳(通达信内核)协议连通性测试……\n")
    try:
        proc = subprocess.run([sys.executable, "scripts/net_diag_tdx.py"],
                              capture_output=True, text=True, timeout=180,
                              cwd=str(Path(__file__).resolve().parents[1]))
        for line in (proc.stdout or "").splitlines():
            log.write("   " + line + "\n")
        if proc.returncode != 0:
            log.write("   探针退出码非 0（盘后节点常吐空数据——盘中重试即可）\n")
    except Exception as e:  # noqa: BLE001
        log.write(f"   探针异常（不影响监控）：{e}\n")
    log.flush()


def _summary(polls: list, trig_all: pd.DataFrame, day: str, log, t0) -> None:
    log.write(f"\n==== 收工总结 {datetime.now():%H:%M:%S}（运行 {(_t_end(t0)):.0f} 分钟）====\n")
    log.write(f"轮询次数 {len(polls)} | 触发事件 {len(trig_all)}\n")
    if len(trig_all):
        log.write("按规则计数：" + json.dumps(trig_all["rule"].value_counts().to_dict(),
                                           ensure_ascii=False) + "\n")
        top = trig_all.sort_values("pct_chg", ascending=False).head(10)
        for _, r in top.iterrows():
            log.write(f"   {r['time']:%H:%M} [{r['rule']}] {r['code']} {r['name']} "
                      f"{r['pct_chg']:+.2f}% 量比 {r['量比']}\n")
    log.write("收盘后别忘了：金太阳客户端 → 盘后数据下载（vipdoc 离线历史就有了）\n")
    log.write("晚上 19:00 定时任务自动日更（无需操作）。下一个硬节点：11-20 四周偏差归因。\n")


def _t_end(t0) -> float:
    return (time.perf_counter() - t0) / 60


def main() -> None:
    t0 = time.perf_counter()
    today = datetime.now().strftime("%Y%m%d")
    day = str(datetime.now().date())
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"monitor_{today}.log"
    trig_path = LOG_DIR / f"triggers_{today}.csv"
    poll_path = LOG_DIR / f"polls_{today}.parquet"

    log = open(log_path, "a", encoding="utf-8")
    log.write(f"\n==== 盯盘监控启动 {datetime.now():%Y-%m-%d %H:%M:%S} ====\n")
    log.write("触发规则（预注册）：R1 动量 5%/8% | R2 近板 7.8%/17.8% | R3 量比≥3 且涨幅≥3%"
              " | 限频 45/90/45 分钟 | 只读快照，不写账本不自动下单\n")
    log.write("介入时刻（预注册）：M1 追涨介入（首越3%且距板≥2pp）| M2 近板介入（未封死）"
              " | M3 出场参考（峰值回撤≥2pp）| 每标的每日一次，次日自动回填 T+1 结果\n")

    # 交易日校验
    try:
        cal = set(pd.to_datetime(pd.read_parquet(
            STORE_DIR / "meta" / "trade_calendar.parquet", columns=["date"])["date"]).dt.date)
        if datetime.now().date() not in cal:
            log.write("今天不是交易日（trade_calendar 无此日）→ 仅跑 TDX 探针后退出\n")
            _run_tdx_probe(log)
            log.close()
            print("非交易日，探针已跑完（见日志），按任意键退出。")
            input()
            return
    except FileNotFoundError:
        log.write("[warn] trade_calendar 缺失，跳过交易日校验\n")

    _run_tdx_probe(log)

    # 昨日介入时刻的 T+1 结果回填（日更链 19:00 已入库）
    from src.data.storage import ParquetStore as _PS
    y_mom = LOG_DIR / f"entry_moments_{(datetime.now() - timedelta(days=1)).strftime('%Y%m%d')}.csv"
    _backfill_moments(y_mom, _PS(), log)

    # 会话窗口：早于 09:20 → 等待；晚于 15:05 → 收工
    now = datetime.now()
    start_ts = pd.Timestamp(f"{day} {SESSION_START}").to_pydatetime()
    end_ts = pd.Timestamp(f"{day} {SESSION_END}").to_pydatetime()
    if now < start_ts:
        wait = (start_ts - now).total_seconds()
        log.write(f">> 等待开盘 {wait/60:.0f} 分钟（09:20 开工）\n")
        log.flush()
        time.sleep(max(wait, 0))
    if datetime.now() >= end_ts:
        log.write("已过收盘（15:05）→ 仅探针，退出\n")
        log.close()
        print("已收盘，按任意键退出。")
        input()
        return

    print(f"监控运行中：{SESSION_START}~{SESSION_END}，日志 {log_path}")
    mom_path = LOG_DIR / f"entry_moments_{today}.csv"
    m_state: dict = {}
    state: dict = {}
    polls: list = []
    trig_frames: list = []
    mom_frames: list = []
    consec_fail = 0
    poll_no = 0

    while datetime.now() < end_ts and poll_no < MAX_POLLS:
        loop_t = datetime.now()
        tm = loop_t.time()
        if LUNCH[0] <= tm <= LUNCH[1]:
            log.write(f"[{tm}] 午间休市，暂停轮询\n")
            log.flush()
            time.sleep(60)
            continue
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                # 盯盘是**轮询**：单次失败直接跳过、下一轮再来，比 3×45s 长重试便宜得多
                # （2026-11-18 实测：东财间歇断连，akshare 分页 56 次请求本身就要 ~70s；
                #  长重试会让一次失败占掉 135s，白丢两三根快照）
                df = fetch_spot(max_attempts=1, delay_s=0)
            watch = _watchable(df)
            poll_no += 1
            consec_fail = 0
            snap = watch[["代码", "名称", "最新价", "涨跌幅", "量比", "换手率",
                          "涨速", "昨收", "5分钟涨跌"]].copy()
            snap["poll_time"] = loop_t
            polls.append(snap)
            hits = _trigger_checks(watch, state, loop_t)
            if len(hits):
                trig_frames.append(hits)
                log.write(f"[{tm}] 触发 {len(hits)} 条：" +
                          "；".join(f"{h['rule']} {h['code']} {h['name']} {h['pct_chg']:+.1f}%"
                                    for _, h in hits.head(6).iterrows()) +
                          ("…" if len(hits) > 6 else "") + "\n")
                pd.concat(trig_frames, ignore_index=True).to_csv(
                    trig_path, index=False, encoding="utf-8-sig")
            moments = _entry_moments(watch, m_state, loop_t)
            if len(moments):
                mom_frames.append(moments)
                pd.concat(mom_frames, ignore_index=True).to_csv(
                    mom_path, index=False, encoding="utf-8-sig")
                log.write(f"[{tm}] 介入时刻 {len(moments)} 条：" +
                          "；".join(f"{h['kind']} {h['code']} {h['pct_chg']:+.1f}%"
                                    for _, h in moments.head(5).iterrows()) +
                          ("…" if len(moments) > 5 else "") + "\n")
            if poll_no % 10 == 0 or len(polls) == 1:
                pd.concat(polls, ignore_index=True).to_parquet(poll_path, index=False)
            log.write(f"[{tm}] 轮询 #{poll_no} 完成（{len(watch)} 只，"
                      f"累计触发 {sum(len(f) for f in trig_frames)}）\n")
            log.flush()
            print(f"[{tm}] #{poll_no} 监听 {len(watch)} 只，触发累计 "
                  f"{sum(len(f) for f in trig_frames)}")
        except Exception as e:  # noqa: BLE001
            consec_fail += 1
            log.write(f"[{datetime.now():%H:%M:%S}] 轮询失败 {consec_fail}"
                      f"/{MAX_CONSEC_FAIL}（连续）：{type(e).__name__}: {str(e)[:120]}\n")
            log.flush()
            if consec_fail >= MAX_CONSEC_FAIL:
                log.write("[大退避] 连续失败过多，休眠 300s（数据资产要紧，不退出）\n")
                time.sleep(300)
        # 轮询节奏
        elapsed = (datetime.now() - loop_t).total_seconds()
        time.sleep(max(POLL_SLEEP_S - elapsed, 5))

    if trig_frames:
        pd.concat(trig_frames, ignore_index=True).to_csv(
            trig_path, index=False, encoding="utf-8-sig")
    if mom_frames:
        pd.concat(mom_frames, ignore_index=True).to_csv(
            mom_path, index=False, encoding="utf-8-sig")
    if polls:
        pd.concat(polls, ignore_index=True).to_parquet(poll_path, index=False)
    trig_all = (pd.concat(trig_frames, ignore_index=True)
                if trig_frames else pd.DataFrame(columns=["rule"]))
    _summary(polls, trig_all, day, log, t0)
    log.close()
    print("监控收工（总结见日志）。")


if __name__ == "__main__":
    main()
