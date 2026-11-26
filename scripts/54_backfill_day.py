"""第 54 步：影子盘缺口回填（D-101，2026-11-24）。

背景：11-23（周一，交易日）19:00 任务计划未触发（机器休眠/关机，WakeToRun=false），
次日控制台手动跑 daily 时行情缺口被自动修复（D-083 链路），但流水线只处理
"最新数据日"——11-23 的账本快照/日报/邮件被静默跳过，净值曲线出现整日空洞。

本脚本：对指定交易日（或自动检测缺口日）按"当天视角"重放流水线：
  - 面板截断到 ≤ 当天（run_pipeline 的 _panel 参数）；
  - 状态取当前账本（无成交在途时与历史状态一致），按**当天收盘价**重估净值；
  - 写快照/持仓/日报（INSERT OR REPLACE，幂等，重复跑安全）；
  - 四账户合并补发一封邮件（主题带「补发」字样）。

边界：仅适用于"缺口日无待成交订单"的纯估值回填（绝大多数缺口日都如此——
20 日调仓网格上挂单日本来就稀疏）。若缺口日有未撮合的 pending 订单，
先人工核对再跑（撮合会按当天开盘价补记账本）。

用法: python scripts/54_backfill_day.py [YYYY-MM-DD|auto] [--dry-run] [--no-mail]
"""
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.storage import ParquetStore
from src.live.pipeline import _load_panel, combine_reports, run_pipeline
from src.strategies.registry import enabled_accounts

DRY_RUN = "--dry-run" in sys.argv
NO_MAIL = "--no-mail" in sys.argv


def detect_gap_day() -> str | None:
    """找最早的缺口日：有行情分区、是交易日、晚于全部账户的首快照、但 large 账本没有快照。

    首快照约束：账户开账之前的日子（各账户 10 月陆续启动）不是缺口，是"还不存在"。
    """
    import sqlite3
    cal = pd.to_datetime(pd.read_parquet(STORE_DIR / "meta" / "trade_calendar.parquet")["date"])
    starts = []
    for db in ("ledger.sqlite", "ledger_2000.sqlite", "ledger_div.sqlite", "ledger_topk.sqlite"):
        con = sqlite3.connect(f"file:{STORE_DIR / 'sim' / db}?mode=ro", uri=True)
        try:
            d = pd.read_sql("SELECT MIN(date) d FROM account_snapshot", con)["d"].iloc[0]
        finally:
            con.close()
        if d:
            starts.append(str(d))
    floor = min(starts) if starts else "2018-01-01"
    con = sqlite3.connect(f"file:{STORE_DIR / 'sim' / 'ledger.sqlite'}?mode=ro", uri=True)
    have = set(pd.read_sql("SELECT DISTINCT date FROM account_snapshot", con)["date"])
    con.close()
    today = pd.Timestamp.now().normalize()
    for d in sorted(cal[(cal >= pd.Timestamp(floor)) & (cal < today)]):
        ds = str(d.date())
        if ds in have:
            continue
        if (STORE_DIR / "market" / "daily_by_date" / f"{ds}.parquet").exists():
            return ds
    return None


def main() -> None:
    arg = next((a for a in sys.argv[1:] if not a.startswith("--")), "auto")
    day = detect_gap_day() if arg == "auto" else arg
    if day is None:
        print(">> 未检测到缺口日（行情分区与账本快照对齐）")
        return
    print(f">> 回填 {day}（dry_run={DRY_RUN}）")

    store = ParquetStore()
    cutoff = pd.Timestamp(day)
    panels = {}          # strategy 加载器缓存（corrected/topkdrop 同面板）
    results = []

    def panel_for(strategy: str):
        key = "dividend" if strategy == "dividend" else "base"
        if key not in panels:
            panels[key] = _load_panel(store, "000300", with_dividend=(key == "dividend"))
        p = panels[key]
        return p[p["date"] <= cutoff].copy()

    for acc, cfg in enabled_accounts().items():
        print(f"\n===== {acc}（{cfg['strategy']}）=====")
        try:
            r = run_pipeline(capital=cfg["capital"], push=False, dry_run=DRY_RUN,
                             db_path=STORE_DIR / "sim" / cfg["ledger"],
                             label=cfg["reports"], strategy=cfg["strategy"],
                             max_positions=cfg["max_positions"],
                             _panel=panel_for(cfg["strategy"]))
            results.append((cfg["group"], r))
        except Exception as e:  # noqa: BLE001  单账户失败不拖垮回填
            import traceback
            traceback.print_exc()
            results.append((cfg["group"], {"date": None, "nav": None,
                                           "day_ret": None, "cum_ret": None,
                                           "report_text": f"# 账户 {acc} 回填失败：{e}"}))

    if DRY_RUN or NO_MAIL:
        print("\n>> 跳过邮件（dry-run / --no-mail）")
        return
    marker = STORE_DIR / "reports" / f".backfill_sent_{day}"
    if marker.exists():
        print(f"\n>> {day} 的补发邮件此前已发过（{marker.name}），跳过——重跑不会重复打扰收件箱")
        return
    from src.live.notifier import is_configured, send_report
    if not is_configured():
        print(">> SMTP 未配置，跳过补发")
        return
    body = combine_reports(day, [{"title": t, "text": r["report_text"], "nav": r["nav"],
                                 "day_ret": r.get("day_ret"), "cum_ret": r.get("cum_ret")}
                                for t, r in results])
    note = (f"\n\n> 补发说明：{day} 的 19:00 定时任务未触发，本邮件由缺口回填"
            f"（54 号脚本，D-101）于 {pd.Timestamp.now():%Y-%m-%d %H:%M} 补发；"
            f"净值按 {day} 收盘价重估。\n")
    subj = f"QuantDex 影子盘日报 {day}（补发）"
    ok = send_report(subj, body + note)
    if ok:
        marker.write_text(f"backfill mail sent {pd.Timestamp.now()}\n", encoding="utf-8")
    print(f"\n>> 补发邮件 {'已发送' if ok else '失败（可重跑 54 号，幂等）'}")


if __name__ == "__main__":
    main()
