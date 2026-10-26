# -*- coding: utf-8 -*-
"""
每日一键：更新 PIT 历史成员行情 → 影子盘流水线（账户由注册表驱动，D-039）→ 合并日报邮件。

账户：
  - large：100 万虚拟，现行策略（低换手+低波+反转 Top50 等权）
  - small：2000 元虚拟，整手小组合（最多 3 只），实盘建仓模拟
  - div  ：100 万虚拟，红利版（低换手+低波+股息率，D-027）——与现行策略并行对比
  - topk ：100 万虚拟，TopkDropout 换手机制（同 corrected 因子，D-031）——换手 A/B
邮件：四账户合并为一封（含账户速览，D-030）；同一交易日只推一次（D-029）。

用法：
    python scripts/10_daily_run.py                 # 完整每日流程（四账户 + 一封合并邮件）
    python scripts/10_daily_run.py --no-push        # 不发送邮件
    python scripts/10_daily_run.py --dry-push       # 只构建邮件正文、不发送（验证推送分支用）
"""
import sys

import _bootstrap  # noqa: F401

from config import DEFAULT_START_DATE, STORE_DIR
from src.data import universe
from src.data.market import update_kline_batch
from src.data.storage import ParquetStore
from src.live.pipeline import run_pipeline


def main() -> None:
    import os

    push = "--no-push" not in sys.argv
    if "--skip-update" not in sys.argv:
        store = ParquetStore()
        symbols = universe.pit_union_symbols("000300", store)
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
    else:
        print(">> 跳过行情更新（--skip-update，使用本地已有数据）")

    print("")

    from src.strategies.registry import enabled_accounts
    results = []
    for key, cfg in enabled_accounts().items():
        try:
            r = run_pipeline(capital=cfg["capital"], push=False,
                             db_path=STORE_DIR / "sim" / cfg["ledger"],
                             label=cfg["reports"], strategy=cfg["strategy"],
                             max_positions=cfg["max_positions"])
        except Exception as e:  # noqa: BLE001  R3-25：单账户失败不拖垮其余账户与合并邮件
            import traceback
            traceback.print_exc()
            r = {"date": None, "new_report": False, "nav": None,
                 "day_ret": None, "cum_ret": None,
                 "report_text": f"# 账户 {key} 流水线失败：{e}"}
        results.append((cfg["group"], r))

    # 合并日报：四账户一封邮件（D-030/D-031）；沿用 D-029 去重——全部账户都推送过则跳过
    if push:
        # results 已由注册表循环填充：(分组标题, 单账户结果)
        if not any(r.get("new_report") for _, r in results):
            print("\n>> 所有账户日报均已推送过（重复运行），跳过合并邮件")
        else:
            from src.live.notifier import is_configured, send_report
            from src.live.pipeline import combine_reports
            day = results[0][1]["date"]
            body = combine_reports(day, [
                {"title": t, "text": r["report_text"], "nav": r["nav"],
                 "day_ret": r.get("day_ret"), "cum_ret": r.get("cum_ret")}
                for t, r in results])
            if not is_configured():
                print("\n>> SMTP 未配置，跳过合并日报推送")
            elif "--dry-push" in sys.argv:
                # 只构建正文不发送：用于验证推送分支（不惊动收件箱）
                print(body[:600])
            else:
                ok = send_report(f"QuantDex 影子盘日报 {day}", body)
                if not ok:
                    sys.exit(2)  # 让计划任务/调度器能看出"推送失败"


if __name__ == "__main__":
    main()
