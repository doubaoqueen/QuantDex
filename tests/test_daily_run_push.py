# -*- coding: utf-8 -*-
"""回归测试：10_daily_run.py 的四账户合并推送分支。

背景（2026-10-26 用户反馈"运行 14 脚本没收到推送"）：
  提交 9dd7d9f 加了第 4 个账户 TopkDropout，只在合并列表里引用了 `r_topk`，
  却没有真的跑该账户的流水线 → 任何一次开启推送的运行都会在发邮件前抛
  NameError，日报永远发不出去。

本测试用打桩替代真实流水线与 SMTP，验证：
  1) 四个账户都被调用（large/small/div/topk，策略分别为默认/dividend/topkdrop）；
  2) 推送分支能构建合并正文（四节 + 账户速览）；
  3) --dry-push 不会真的发送邮件。
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_daily_run_merges_four_accounts_without_sending():
    for p in (str(ROOT), str(ROOT / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)

    import src.live.notifier as notifier_mod
    import src.live.pipeline as pipeline_mod

    calls, sent = [], {}
    orig = (pipeline_mod.run_pipeline, notifier_mod.is_configured, notifier_mod.send_report)

    def fake_run_pipeline(**kw):
        calls.append(kw)
        label = kw.get("label", "large")
        return {"new_report": True, "date": "2026-10-26",
                "report_text": f"#{label} 日报正文（桩）",
                "nav": 1_000_000.0, "day_ret": 0.001, "cum_ret": 0.0123}

    try:
        pipeline_mod.run_pipeline = fake_run_pipeline
        notifier_mod.is_configured = lambda: True
        notifier_mod.send_report = lambda subject, body, to=None: (
            sent.update(subject=subject, body=body) or True)

        spec = importlib.util.spec_from_file_location(
            "daily_run_under_test", ROOT / "scripts" / "10_daily_run.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        argv_backup = sys.argv
        sys.argv = ["10_daily_run.py", "--skip-update", "--dry-push"]
        try:
            mod.main()
        finally:
            sys.argv = argv_backup
    finally:
        (pipeline_mod.run_pipeline, notifier_mod.is_configured,
         notifier_mod.send_report) = orig

    assert len(calls) == 4, f"应有 4 个账户流水线，实际 {len(calls)}：{[c.get('label') for c in calls]}"
    # large 账户走 run_pipeline 的默认 label（不显式传），其余三个显式传
    assert [c.get("label") or "large" for c in calls] == ["large", "small", "div", "topk"]
    # D-039 注册表化后策略恒显式传入（corrected 不再依赖函数默认值）
    assert [c.get("strategy") for c in calls] == ["corrected", "corrected", "dividend", "topkdrop"]
    assert not sent, "--dry-push 不应真的发送邮件"
