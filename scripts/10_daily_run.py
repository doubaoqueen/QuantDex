# -*- coding: utf-8 -*-
"""
每日一键：更新 PIT 历史成员行情 → 影子盘流水线（三个账户）→ 合并日报邮件。

账户：
  - large：100 万虚拟，现行策略（低换手+低波+反转 Top50 等权）
  - small：2000 元虚拟，整手小组合（最多 3 只），实盘建仓模拟
  - div  ：100 万虚拟，红利版（低换手+低波+股息率，D-027）——与现行策略并行对比
邮件：三账户合并为一封（含账户速览，D-030）；同一交易日只推一次（D-029）。

用法：
    python scripts/10_daily_run.py              # 完整每日流程（三账户 + 一封合并邮件）
    python scripts/10_daily_run.py --no-push    # 不发送邮件
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
        print(f">> 更新 PIT 历史成员行情（{len(symbols)} 只，增量，source={source}，"
              f"baostock兜底={allow_bs}）...")
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

    print("\n>> [账户 large] 影子盘流水线（100 万 / Top50 等权）...")
    r_large = run_pipeline(push=False)

    print("\n>> [账户 small] 影子盘流水线（2000 元 / 整手 ≤3 只）...")
    r_small = run_pipeline(capital=2000.0, push=False,
                           db_path=STORE_DIR / "sim" / "ledger_2000.sqlite",
                           label="small", max_positions=3)

    print("\n>> [账户 div] 影子盘流水线（100 万 / 红利版：低换手+低波+股息率）...")
    r_div = run_pipeline(push=False,
                         db_path=STORE_DIR / "sim" / "ledger_div.sqlite",
                         label="div", strategy="dividend")

    # 合并日报：三账户一封邮件（D-030）；沿用 D-029 去重——全部账户都推送过则跳过
    if push:
        results = [("大账户（100万）", r_large), ("小账户（2000元）", r_small),
                   ("红利账户（100万）", r_div)]
        if not any(r.get("new_report") for _, r in results):
            print("\n>> 所有账户日报均已推送过（重复运行），跳过合并邮件")
        else:
            from src.live.notifier import is_configured, send_report
            from src.live.pipeline import combine_reports
            day = r_large["date"]
            body = combine_reports(day, [
                {"title": t, "text": r["report_text"], "nav": r["nav"],
                 "day_ret": r.get("day_ret"), "cum_ret": r.get("cum_ret")}
                for t, r in results])
            if is_configured():
                ok = send_report(f"QuantDex 影子盘日报 {day}", body)
                print(f"\n>> 合并日报{'已发送' if ok else '发送失败'}：{day}"
                      f"（{len(results)} 个账户）")
            else:
                print("\n>> SMTP 未配置，跳过合并日报推送")


if __name__ == "__main__":
    main()
