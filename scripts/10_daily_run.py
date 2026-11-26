"""
每日一键：更新 PIT 历史成员行情 → 5 分钟线增量（D-060）→ 影子盘流水线
（账户由注册表驱动，D-039）→ 数字/价格质检（D-059）→ 合并日报邮件。

账户：
  - large：100 万虚拟，现行策略（低换手+低波+反转 Top50 等权）
  - small：2000 元虚拟，整手小组合（最多 3 只），实盘建仓模拟
  - div  ：100 万虚拟，红利版（低换手+低波+股息率，D-027）——与现行策略并行对比
  - topk ：100 万虚拟，TopkDropout 换手机制（同 corrected 因子，D-031）——换手 A/B
邮件：四账户合并为一封（含账户速览，D-030）；同一交易日只推一次（D-029）。
退出码：0 正常；2 邮件推送失败；3 拿不到流水线锁；4 账本自检异常（D-059）；
5 质检/维护异常——数字审计/价格完整性/分红刷新/因子监控 FAIL（D-059/D-062），
计划任务/调度器据此告警。

用法：
    python scripts/10_daily_run.py                 # 完整每日流程（四账户 + 一封合并邮件）
    python scripts/10_daily_run.py --no-push        # 不发送邮件
    python scripts/10_daily_run.py --dry-push       # 只构建邮件正文、不发送（验证推送分支用）
    python scripts/10_daily_run.py --skip-audit     # 跳过 19/25 质检（调试用）
"""
import sys

import _bootstrap  # noqa: F401

from config import BASE_DIR, DEFAULT_START_DATE, STORE_DIR
from src.data import universe
from src.data.market import update_kline_batch
from src.data.storage import ParquetStore
from src.live.pipeline import run_pipeline


def _run_audit(script: str, *args: str) -> bool:
    """跑一个质检/维护脚本（子进程隔离，stdout 直落调度日志），FAIL 返回 True。"""
    import subprocess
    rc = subprocess.run([sys.executable, str(BASE_DIR / "scripts" / script), *args],
                        cwd=str(BASE_DIR)).returncode
    return rc != 0


def _update_minutes(symbols, store) -> None:
    """5 分钟线每日增量（D-060）；失败只告警、不阻断日报。

    D-090：从「更新行情」里挪到**邮件之后**——这一段是最慢的（949 只约 10~20 分钟，
    2026-11-16 实测 19:00 任务被它拖到 19:33 才发信），而日报/邮件根本不依赖它。
    独立 .minute.lock 与 29 号回填互斥（拿不到=回填在跑，今晚跳过）；只增量不回填。
    """
    # 分时数据：baostock 5 分钟线每日增量（D-060）。收盘后运行，失败不阻断
    # 日线与流水线（QUANTDEX_MINUTE=0 可关闭）。与 29 号回填互斥用独立
    # .minute.lock（拿不到=回填在跑，跳过）；只增量不回填（回填约 3 分钟/只）。
    import os

    if os.environ.get("QUANTDEX_MINUTE", "1") != "0":
        from src.live.proclock import try_lock as _try_minute_lock
        mlf = _try_minute_lock(STORE_DIR / "sim" / ".minute.lock")
        if mlf is None:
            print(">> [minute] 5 分钟线回填脚本正在运行，今晚增量跳过")
        else:
            try:
                from src.data.market import update_minute_batch
                print(">> 更新 5 分钟线（baostock）...")
                mr = update_minute_batch(
                    symbols, freq="5", full_start=DEFAULT_START_DATE,
                    store=store, backfill_missing=False)
                mf = mr.pop("__failed__", [])
                n_up = sum(1 for v in mr.values() if v == "uptodate")
                n_none = sum(1 for v in mr.values() if v == "no-local")
                print(f">> 分钟线完成：处理 {len(mr)} 只（已最新 {n_up}、"
                      f"未回填 {n_none}），失败 {len(mf)} 只")
                for code, err in mf[:5]:
                    print(f"   FAIL {code}: {err}")
            except Exception as e:  # noqa: BLE001
                print(f"[minute] 5 分钟线更新失败（不影响后续流程）: {e}")


def _ledger_gap_days(store, lookback: int = 30) -> list[tuple[str, list[str]]]:
    """交易日有行情分区、但任一账户缺快照的日期（D-101）。

    11-23 事故：19:00 任务未触发 → 行情缺口次日被 02b 修复，但流水线只处理
    "最新数据日"，账本快照/日报/邮件整日静默消失。此守卫让缺口在**当晚**
    的日志与邮件标题上可见（不再等用户翻净值曲线才发现空洞）。
    """
    import sqlite3

    import pandas as pd

    from src.strategies.registry import enabled_accounts as _ea
    cal = pd.to_datetime(
        pd.read_parquet(STORE_DIR / "meta" / "trade_calendar.parquet")["date"])
    snaps: dict[str, set] = {}
    firsts: dict[str, str] = {}
    last = pd.Timestamp("2000-01-01")
    for _k, cfg in _ea().items():
        con = sqlite3.connect(
            f"file:{STORE_DIR / 'sim' / cfg['ledger']}?mode=ro", uri=True)
        try:
            ds = set(pd.read_sql(
                "SELECT DISTINCT date FROM account_snapshot", con)["date"])
        finally:
            con.close()
        snaps[cfg["ledger"]] = ds
        if ds:
            firsts[cfg["ledger"]] = min(ds)
            last = max(last, pd.Timestamp(max(ds)))
    if last == pd.Timestamp("2000-01-01"):
        return []
    window = [d for d in sorted(cal) if last - pd.Timedelta(days=45) < d <= last]
    window = window[-lookback:]
    out: list[tuple[str, list[str]]] = []
    for d in window:
        ds = str(d.date())
        part = STORE_DIR / "market" / "daily_by_date" / f"{ds}.parquet"
        if not part.exists():
            continue        # 行情缺 = 02b 的事，有 gap repair 兜底
        miss = [k for k, ds_set in snaps.items()
                if k in firsts and ds > firsts[k] and ds not in ds_set]
        if miss:
            out.append((ds, miss))
    return out


def main() -> None:
    import os

    from src.live.proclock import try_lock
    lf = try_lock(STORE_DIR / "sim" / ".pipeline.lock")
    if lf is None:
        print(">> [lock] 另一流水线实例正在运行，本次退出（R3-02）")
        sys.exit(3)

    push = "--no-push" not in sys.argv
    store = ParquetStore()
    symbols = universe.pit_union_symbols("000300", store)
    if "--skip-update" not in sys.argv:
        source = os.environ.get("QUANTDEX_SOURCE", "bs")
        allow_sina = os.environ.get("QUANTDEX_SINA_FALLBACK", "0") == "1"
        allow_bs = os.environ.get("QUANTDEX_BS_FALLBACK", "1") == "1"
        r = update_kline_batch(symbols, asset="stock", adjust="qfq",
                               full_start=DEFAULT_START_DATE, source=source,
                               store=store, sina_fallback=allow_sina,
                               bs_fallback=allow_bs)
        failed = r.pop("__failed__", [])
        n_bs = sum(1 for v in r.values() if isinstance(v, str) and v.startswith("bs_fallback"))
        n_up = sum(1 for v in r.values() if v == "uptodate")
        print(f">> 行情更新完成：处理 {len(r)} 只（已最新 {n_up}、baostock兜底 {n_bs}），失败 {len(failed)} 只")
        for code, err in failed[:5]:
            print(f"   FAIL {code}: {err}")
        from src.data.snapshot import update_indexes
        print(">> 更新基准指数（沪深300 / 中证500）...")
        ir = update_indexes(store)
        print(f">> 指数完成：{len(ir)} 个")
        # D-041a 护栏下沉（D-062）：手动/无 02b 的更新路径同样做全量自洽校验+修复
        from src.data.market import ensure_qfq_integrity
        ensure_qfq_integrity(symbols, store=store)
    else:
        print(">> 跳过行情更新（--skip-update，使用本地已有数据）")

    # D-084：候选池快照预计算——Web 端候选池/策略动向/因子面板打开即读缓存
    # （原逐只循环 949 只 ≈5~10s；预计算后交互 <0.1s，且与股票池规模解耦）。
    try:
        from src.analysis.factors import get_factor_snapshot
        snap = get_factor_snapshot(symbols, store=store)
        print(f">> 候选池快照就绪：{len(snap)} 只（as_of {snap.attrs.get('as_of')}）")
    except Exception as e:  # noqa: BLE001  预计算失败不阻断日更（Web 端会现算兜底）
        print(f"[snapshot] 预计算失败（不影响流水线，Web 端现算兜底）: {e}")

    print("")

    from src.live.pipeline import _load_panel
    from src.strategies.registry import enabled_accounts
    results = []
    backfilled: list[str] = []          # D-102：本次自动补回的交易日
    _panels: dict = {}                  # 面板按策略缓存（corrected/topk 同源）

    def _panel_for(strategy: str):
        k = "dividend" if strategy == "dividend" else "base"
        if k not in _panels:
            _panels[k] = _load_panel(store, "000300",
                                       with_dividend=(k == "dividend"))
        return _panels[k]

    for key, cfg in enabled_accounts().items():
        # D-102 自动补流水：账本落后于数据面 N 个交易日时，先把缺口日逐日
        # 重放（面板截断到当日、挂单按当日开盘撮合、当日若是调仓日照常下单），
        # 再跑最新日——人在不在、漏一天两天，净值/日报/邮件都不再有空洞。
        # 幂等：快照 INSERT OR REPLACE、已成交订单不再是 pending 不会重复撮合。
        try:
            import sqlite3 as _sq

            import pandas as _pd
            panel = _panel_for(cfg["strategy"])
            latest = _pd.Timestamp(panel["date"].max())
            con = _sq.connect(
                f"file:{STORE_DIR / 'sim' / cfg['ledger']}?mode=ro", uri=True)
            try:
                have = set(_pd.read_sql(
                    "SELECT DISTINCT date FROM account_snapshot", con)["date"])
            finally:
                con.close()
            if have:
                floor = _pd.Timestamp(min(have))
                miss = sorted(d for d in _pd.DatetimeIndex(panel["date"].unique())
                              if floor < d < latest and str(d.date()) not in have)
                for d in miss:
                    sub = panel[panel["date"] <= d]
                    rb = run_pipeline(
                        capital=cfg["capital"], push=False,
                        db_path=STORE_DIR / "sim" / cfg["ledger"],
                        label=cfg["reports"], strategy=cfg["strategy"],
                        max_positions=cfg["max_positions"], _panel=sub)
                    if rb.get("date"):
                        backfilled.append(f"{key} {rb['date']}")
                        print(f">> [auto-backfill] {key} 补跑 {rb['date']}"
                              f"（nav {rb.get('nav') or float('nan'):,.2f}）")
        except Exception as e:  # noqa: BLE001  补跑失败不拦最新日；缺口守卫会兜底告警
            import traceback
            traceback.print_exc()
            print(f">> [auto-backfill] {key} 补跑失败（继续跑最新日）: {e}")
        try:
            r = run_pipeline(capital=cfg["capital"], push=False,
                             db_path=STORE_DIR / "sim" / cfg["ledger"],
                             label=cfg["reports"], strategy=cfg["strategy"],
                             max_positions=cfg["max_positions"],
                             _panel=_panel_for(cfg["strategy"]))
        except Exception as e:  # noqa: BLE001  R3-25：单账户失败不拖垮其余账户与合并邮件
            import traceback
            traceback.print_exc()
            r = {"date": None, "new_report": False, "nav": None,
                 "day_ret": None, "cum_ret": None,
                 "report_text": f"# 账户 {key} 流水线失败：{e}"}
        results.append((cfg["group"], r))

    # D-101：账本缺口守卫——交易日有行情但没快照（任务未触发/被静默跳过），
    # 当晚就必须看见，不能再等净值曲线出空洞（11-23 教训）
    try:
        ledger_gaps = _ledger_gap_days(store)
    except Exception as e:  # noqa: BLE001  守卫本身不许把日更打挂
        ledger_gaps = []
        print(f">> [gap-ledger] 缺口检查失败（不影响流程）: {e}")
    if ledger_gaps:
        for ds, miss in ledger_gaps:
            print(f">> [gap-ledger] ⚠️ {ds} 有行情但账本缺快照：{', '.join(miss)}")
        print(">> [gap-ledger] 用 python scripts/54_backfill_day.py 补跑这些日子"
              "（幂等，含补发邮件）")

    # D-059：自检/审计异常必须在"推送标题与退出码"两个层面可见——
    # 事故复盘教训 4："异常值要当信号"，只写进日报文本等于没人响应。
    check_bad = [(t, r) for t, r in results if r.get("self_check_ok") is False]
    for t, _ in check_bad:
        print(f">> [self-check] ⚠️ 账户 {t} 自检未通过（负现金/股数不符/权重超限），详见其日报")
    audit_bad = False
    if "--skip-audit" not in sys.argv:
        # 数字审计每日跑（净值恒等式/现金连续性/成交金额，秒级）。
        import datetime as _dt
        if _run_audit("19_audit_numbers.py"):
            audit_bad = True
            print(">> [audit] 数字审计 FAIL，详见上方输出")
        if _dt.date.today().weekday() == 5:
            # 周六例行维护（D-062）：
            # ① 分红/复权因子增量刷新（baostock，与 5 分钟回填互斥——bs 双进程并发未验证）
            from src.live.proclock import try_lock as _try_minute_lock
            mlf = _try_minute_lock(STORE_DIR / "sim" / ".minute.lock")
            if mlf is None:
                print(">> [maintenance] 5 分钟回填占用 baostock，本周分红/因子刷新顺延")
            else:
                if _run_audit("12_fetch_dividends.py"):
                    audit_bad = True
                    print(">> [maintenance] 分红刷新有失败，详见上方输出")
                if _run_audit("20_fetch_reference_data.py", "adjfactor"):
                    audit_bad = True
                    print(">> [maintenance] 复权因子刷新有失败，详见上方输出")
            # ② 价格完整性（全库扫描，约 1-2 分钟）；③ 因子月度监控（离线）
            if _run_audit("25_check_price_integrity.py"):
                audit_bad = True
                print(">> [audit] 价格完整性检测发现断裂，详见上方输出")
            if _run_audit("31_factor_monitor.py"):
                audit_bad = True
                print(">> [monitor] 在用因子出现衰减预警，详见上方输出")

    # 合并日报：四账户一封邮件（D-030/D-031）；沿用 D-029 去重——全部账户都推送过则跳过。
    # D-062：发送失败落标记（.combine_send_failed），重跑时不再被"都已推送过"挡住
    combine_fail = STORE_DIR / "reports" / ".combine_send_failed"
    if push:
        # results 已由注册表循环填充：(分组标题, 单账户结果)
        if not any(r.get("new_report") for _, r in results) and not combine_fail.exists():
            print("\n>> 所有账户日报均已推送过（重复运行），跳过合并邮件")
        else:
            from src.live.notifier import is_configured, send_report
            from src.live.pipeline import combine_reports
            # 首账户失败时其 date 为 None——取第一个有效日期，避免标题出现"日报 None"
            day = next((r.get("date") for _, r in results if r.get("date")), None)
            body = combine_reports(day, [
                {"title": t, "text": r["report_text"], "nav": r["nav"],
                 "day_ret": r.get("day_ret"), "cum_ret": r.get("cum_ret")}
                for t, r in results])
            if backfilled:   # D-102：补回的日子写进邮件正文，收件人一眼可见
                body = ("> ⚠️ 本次运行自动补回缺失交易日：" + "、".join(backfilled) +
                        "（按各日收盘价重估，含当日撮合）\n\n" + body)
            if not is_configured():
                print("\n>> SMTP 未配置，跳过合并日报推送")
            elif "--dry-push" in sys.argv:
                # 只构建正文不发送：用于验证推送分支（不惊动收件箱）
                print(body[:600])
            else:
                subj = f"QuantDex 影子盘日报 {day}"
                if check_bad:
                    subj = f"⚠️自检异常 {subj}"
                elif audit_bad:
                    subj = f"⚠️审计异常 {subj}"
                elif ledger_gaps:
                    subj = f"⚠️账本缺口 {subj}"
                ok = send_report(subj, body)
                if ok:
                    combine_fail.unlink(missing_ok=True)
                else:
                    combine_fail.write_text(
                        f"combine send failed {day}\n", encoding="utf-8")
                    sys.exit(2)  # 让计划任务/调度器能看出"推送失败"（下次运行重试）
    # D-090：5 分钟线增量放在邮件之后
    if "--skip-update" not in sys.argv and "--skip-minute" not in sys.argv:
        _update_minutes(symbols, store)

    # 涨停池每日采集入链（2026-11-23，TODO 周末清单收口）：东财池子保留期仅约
    # 1 个月，**过期即失**——每天不采就丢一天 D-093 的研究原料。50 号自带幂等
    # （date+code 去重覆盖）与 15:05 盘中守卫；重抓整个保留窗口顺带修复此前
    # 失败的日子。失败只告警不阻断（研究原料，非质检门槛，不进退出码）；
    # QUANTDEX_ZTPOOL=0 可关闭。放分钟线之后（东财单请求，不占 baostock）。
    if os.environ.get("QUANTDEX_ZTPOOL", "1") != "0":
        print(">> 涨停池采集（东财，保留窗口 26 交易日，幂等）...")
        if _run_audit("50_ztpool_backfill.py"):
            print(">> [ztpool] 采集失败——保留期 1 个月，连续失败会丢研究原料，请检查日志")

    if check_bad:
        sys.exit(4)  # D-059：自检异常——数据可能有 D-053 同类指纹，优先处理
    if audit_bad:
        sys.exit(5)  # D-059：质检异常——对账/复权断裂，见调度日志


if __name__ == "__main__":
    main()
