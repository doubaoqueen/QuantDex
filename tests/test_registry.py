# -*- coding: utf-8 -*-
"""策略注册表一致性回归（D-039）：加账户漏改一处即在此报错。"""
import importlib.util
import sys
from pathlib import Path

from src.strategies.registry import STRATEGIES, enabled_accounts


def _load_webapp():
    spec = importlib.util.spec_from_file_location(
        "webapp_mod", Path(__file__).resolve().parents[1] / "scripts" / "16_webapp.py")
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        spec.loader.exec_module(m)
    finally:
        sys.path.pop(0)
    return m


def test_registry_schema_complete():
    for k, c in STRATEGIES.items():
        for field in ("label", "strategy", "capital", "ledger", "reports",
                      "backtest_suffix", "max_positions", "enabled", "group"):
            assert field in c, f"{k} 缺字段 {field}"
        assert c["ledger"].endswith(".sqlite")
        assert c["backtest_suffix"] in ("", "_div", "_tk")


def test_webapp_accounts_match_registry():
    m = _load_webapp()
    assert set(m.ACCOUNTS.keys()) == set(enabled_accounts().keys())
    for k, c in enabled_accounts().items():
        assert m.ACCOUNTS[k]["label"] == c["label"]
        assert m.ACCOUNTS[k]["suffix"] == c["backtest_suffix"]
        assert m.ACCOUNTS[k]["db"].name == c["ledger"]


def test_webapp_strategies_endpoint_shape():
    m = _load_webapp()
    accounts = [{"key": k, "label": v["label"], "strategy": v["group"]}
                for k, v in m.ACCOUNTS.items()]
    assert {a["key"] for a in accounts} == set(enabled_accounts().keys())
