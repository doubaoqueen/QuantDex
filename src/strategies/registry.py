# -*- coding: utf-8 -*-
"""策略/账户注册表（D-039）：账户定义的**单一事实来源**。

此前账户信息散落 5 处硬编码（10_daily_run、webapp.ACCOUNTS、backtest 后缀映射、
candidates 分支、前端 App.vue），加一个账户要改五处、漏一处即静默出错
（外部评审报告2 P0-1 的风险类别）。现在：新增账户/策略只改本文件。

字段：label(显示名) strategy(05/06/pipeline 的策略键) capital ledger(账本文件名)
      reports(日报目录) backtest_suffix(回测产物后缀) max_positions group(前端分组)
"""
from __future__ import annotations

STRATEGIES: dict[str, dict] = {
    "large": {"label": "大账户（100万）", "strategy": "corrected",
              "capital": 1_000_000.0, "ledger": "ledger.sqlite",
              "reports": "large", "backtest_suffix": "", "max_positions": None,
              "enabled": True, "group": "现行版 · 低换手+低波+反转"},
    "small": {"label": "小账户（2000元 · 执行验证）", "strategy": "corrected",
              "capital": 2_000.0, "ledger": "ledger_2000.sqlite",
              "reports": "small", "backtest_suffix": "", "max_positions": 3,
              "enabled": True, "group": "现行版 · 低换手+低波+反转"},
    "div": {"label": "红利账户（100万）", "strategy": "dividend",
            "capital": 1_000_000.0, "ledger": "ledger_div.sqlite",
            "reports": "div", "backtest_suffix": "_div", "max_positions": None,
            "enabled": True, "group": "红利版 · +股息率"},
    "topk": {"label": "换手账户（100万）", "strategy": "topkdrop",
             "capital": 1_000_000.0, "ledger": "ledger_topk.sqlite",
             "reports": "topk", "backtest_suffix": "_tk", "max_positions": None,
             "enabled": True, "group": "TopkDropout · 换手上界"},
}


def enabled_accounts() -> dict[str, dict]:
    return {k: c for k, c in STRATEGIES.items() if c.get("enabled")}
