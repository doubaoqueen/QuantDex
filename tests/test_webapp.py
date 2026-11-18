"""webapp 安全与挂单校验回归（D-034）：路径遍历防护 / 买入现金 / 卖出持仓。"""
import importlib.util
import sys
from pathlib import Path

# 以文件方式加载 scripts/16_webapp.py（scripts 不是包）
_spec = importlib.util.spec_from_file_location(
    "webapp_mod", Path(__file__).resolve().parents[1] / "scripts" / "16_webapp.py")
webapp = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    _spec.loader.exec_module(webapp)
finally:
    sys.path.pop(0)


def test_report_path_traversal_blocked():
    out = webapp.report_text("large", "../../../.env")
    assert ".env" not in out and "SMTP" not in out
    assert "未找到" in out or "非法" in out
    assert webapp.report_text("hacker", "2026-10-23").startswith("未找到")


def test_add_order_rejects_insufficient_cash_and_naked_sell():
    from src.live.ledger import Ledger
    tmp = Path("data/store/sim/_test_webapp.sqlite")
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
    led = Ledger(tmp)
    led.record_day("2026-10-20", cash=100.0, market_value=0.0)
    led.close()

    orig = dict(webapp.ACCOUNTS)
    try:
        webapp.ACCOUNTS["testacc"] = {"db": tmp, "label": "测试"}
        r1 = webapp.add_order("testacc", "600519", "buy", 100)
        assert not r1["ok"] and "现金不足" in r1["msg"], r1
        r2 = webapp.add_order("testacc", "600519", "sell", 100)
        assert not r2["ok"] and "持仓不足" in r2["msg"], r2
    finally:
        webapp.ACCOUNTS.clear()
        webapp.ACCOUNTS.update(orig)
        for suf in ("", "-wal", "-shm"):
            Path(str(tmp) + suf).unlink(missing_ok=True)


def test_auth_token_and_login():
    import hashlib as _hsh
    import hmac as _h
    import time as _t
    tok = webapp._make_token()
    assert webapp._check_token(tok), "有效 token 应通过"
    exp, sig = tok.split(".")
    tampered = exp + "." + ("0" if sig[0] != "0" else "1")
    assert not webapp._check_token(tampered), "篡改签名应被拒"
    expired = f"{int(_t.time()) - 10}." + _h.new(
        webapp.AUTH["secret"].encode(), str(int(_t.time()) - 10).encode(),
        _hsh.sha256).hexdigest()
    assert not webapp._check_token(expired), "过期 token 应被拒"
    assert webapp._verify_login(webapp.AUTH["user"], webapp.AUTH["pass"])
    assert not webapp._verify_login(webapp.AUTH["user"], "wrong")


def test_board_limit_and_limit_tag():
    """涨跌停幅度与标注：主板 ±10%、创/科 ±20%、ST ±5%（展示口径）。"""
    from config import board_limit
    assert board_limit("600519") == 0.10
    assert board_limit("300750") == 0.20
    assert board_limit("688009") == 0.20
    assert board_limit("600519", "ST长油") == 0.05
    assert board_limit("600519", "*ST长油") == 0.05

    tag = webapp._limit_tag
    assert tag(0.10, "600519") == "涨停"
    assert tag(0.0995, "600519") == "涨停"      # 四舍五入到 9.95% 也算封板
    assert tag(0.05, "600519") is None          # 主板涨 5% 不是涨停
    assert tag(0.05, "600519", "ST长油") == "涨停"
    assert tag(-0.1995, "300750") == "跌停"
    assert tag(-0.095, "300750") is None        # 创业板跌 9.5% 不是跌停
    assert tag(None, "600519") is None
    assert tag(float("nan"), "600519") is None
    assert tag("bad", "600519") is None


def test_limit_watch_structure():
    """全池涨跌停观察（D-063）：结构完整、方向正确、与 board_limit 同源。"""
    out = webapp.limit_watch()
    assert out["date"] is not None or out.get("note")
    for item in out["up"] + out["down"]:
        assert set(item) == {"symbol", "name", "industry", "pct"}
    # 涨停必为正收益、跌停必为负，且与判定阈值同源（board_limit 容差 1e-3）；
    # 必须传名称——ST 股 ±5%，2026-11-12 ST棕榈 +7.44% 涨停暴露了漏传导致的假失败
    from config import board_limit
    for x in out["up"]:
        assert x["pct"] >= board_limit(x["symbol"], x["name"]) - 1e-3 > 0
    for x in out["down"]:
        assert x["pct"] <= -(board_limit(x["symbol"], x["name"]) - 1e-3) < 0


def _run_spot_work(spots, holes, d0, auto=False, reports=("large",)):
    """跑一遍 _spot_work（全部外部动作打桩），返回 (started_jobs, notes, state_updates)。"""
    import pandas as pd

    import src.data.snapshot as snap
    started, notes, states = [], [], []
    orig = (snap.missing_trade_days, snap.snapshot_date, snap.update_indexes,
            webapp.update_spot_snapshot, webapp.start_job, webapp._note,
            webapp._report_missing, webapp._set_state)
    try:
        snap.missing_trade_days = lambda *a, **k: list(holes)
        snap.snapshot_date = lambda *a, **k: (pd.Timestamp(d0) if d0 else None)
        snap.update_indexes = lambda *a, **k: {}
        webapp.update_spot_snapshot = lambda *a, **k: dict(spots)
        webapp.start_job = lambda name: (started.append(name), {"ok": True})[1]
        webapp._note = lambda msg: notes.append(msg)
        webapp._report_missing = lambda day: list(reports)
        webapp._set_state = lambda **kw: states.append(dict(kw))
        webapp._job["busy"] = "spot"
        webapp._spot_work(auto=auto)
    finally:
        (snap.missing_trade_days, snap.snapshot_date, snap.update_indexes,
         webapp.update_spot_snapshot, webapp.start_job, webapp._note,
         webapp._report_missing, webapp._set_state) = orig
    return started, notes, states


def test_spot_work_starts_gap_repair_when_day_missing():
    """D-080：快照只能写最近一个收盘日，漏跑的那天必须靠区间重抓补（手动=02b）。"""
    started, notes, _ = _run_spot_work({"ok": True, "date": "2026-11-10"},
                                       ["2026-11-09"], "2026-11-10")
    assert started == ["update"], started           # 手动模式只补行情、不发信
    assert any("[gap]" in n and "2026-11-09" in n for n in notes), notes
    assert any("跑流水线" in n for n in notes), notes   # 手动补完还要提示去补日报


def test_spot_work_auto_chain_runs_daily_on_gap():
    started, notes, _ = _run_spot_work({"ok": True, "date": "2026-11-10"},
                                       ["2026-11-09"], "2026-11-10", auto=True)
    assert started == ["daily"], started            # 自动链补数据+流水线+日报
    assert any("[gap]" in n for n in notes), notes


def test_spot_work_skips_intraday_without_holes():
    """盘中不落库，也不该启动任何后台任务（避免把盘中价写成收盘价）。"""
    started, notes, _ = _run_spot_work({"ok": False, "kind": "intraday"}, [], None)
    assert started == [], started
    assert any("盘中" in n for n in notes), notes


def test_spot_work_reports_when_spot_filled_the_missed_day():
    """凌晨补跑的正确结局：快照把 11-09 补上后不再整批重抓，只跑流水线发日报。"""
    started, notes, _ = _run_spot_work({"ok": True, "date": "2026-11-09"}, [],
                                       "2026-11-09", auto=True)
    assert started == ["report"], started
    assert not any("[gap]" in n for n in notes), notes


def test_spot_work_manual_noop_when_already_current():
    started, _, _ = _run_spot_work({"ok": True, "date": "2026-11-09"}, [],
                                   "2026-11-09", auto=False)
    assert started == [], started


def test_spot_work_releases_cooldown_when_nothing_done():
    """D-081：自动链空跑（盘中）不能占掉 60 分钟节流窗口——否则下一次打开被挡住，
    用户只能手动点，邮件也就不会自己发（2026-11-10 02:03 的真实经过）。"""
    started, _, states = _run_spot_work({"ok": False, "kind": "intraday"}, [], None,
                                        auto=True)
    assert started == [], started
    assert any(s.get("last_auto_update") == 0 for s in states), states



def test_school_payload_shape():
    """/api/school：量化学院扩充后的内容字段齐备（2026-11-13）。"""
    st = webapp.school_data()
    for k in ("pitfalls", "wf_folds", "wf_summary", "wf_lesson", "portfolio_steps",
              "risk_budget", "risk_note", "risk_callout", "postmortem",
              "postmortem_lesson", "ops_rows", "strategies", "accounts"):
        assert k in st and st[k], f"missing school field: {k}"
    assert len(st["pitfalls"]) >= 8
    assert len(st["wf_folds"]) == 5
    assert len(st["risk_budget"]) == 3
    assert len(st["postmortem"]) == 2
    for r in st["ic_rows"]:                     # 本地有 expr_ic_summary 时必须带 FDR 字段
        assert {"ic5", "icir20", "q20", "fdr"} <= set(r)


def test_strategy_outlook_survives_empty_and_short_pool():
    """D-085 回归：候选池为空 / 缺持仓标的时，策略动向不能整页 HTTP 500。

    真实触发：`10_daily_run` 预计算会重写 cache/factor_snapshot.parquet，面板同时读；
    旧实现遇到空表 `df_full.nlargest(50,"score")` 直接 KeyError、遇到"持仓不在面板里"
    (ST 剔除/停牌缺因子) 则在 `df_full.loc[sym]` KeyError —— 都是 HTTP 500。
    """
    import sqlite3

    import pandas as pd

    orig = (webapp.candidates, dict(webapp._CAND_CACHE), webapp._db, webapp._meta_maps)
    try:
        webapp.candidates = lambda acc: {"rows": [], "date": None}
        webapp._meta_maps = lambda: ({}, {})
        webapp._CAND_CACHE.clear()

        # 1) 空面板 → 可读提示，不抛异常
        webapp._CAND_CACHE["t"] = (0, {"rows": []}, pd.DataFrame())
        out = webapp.strategy_outlook("t")
        assert out.get("err"), out

        # 2) 半截面板：持仓 600000 不在面板里 → 仍要出结果（该行 score 为空）
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE positions (date TEXT, symbol TEXT)")
        con.execute("INSERT INTO positions VALUES ('2026-11-17', '600000')")
        con.execute("CREATE TABLE trade_log (status TEXT, fill_date TEXT)")
        webapp._db = lambda acc: con
        df = pd.DataFrame({"score": [0.9, 0.8], "close": [1.0, 2.0]},
                          index=["000001", "000002"])
        df.index.name = "symbol"
        webapp._CAND_CACHE["t"] = (0, {"date": "2026-11-17"}, df)
        out = webapp.strategy_outlook("t")
        assert not out.get("err"), out
        exit_syms = [r["symbol"] for r in out["rows_exit"]]
        assert exit_syms == ["600000"], out["rows_exit"]
        assert out["rows_exit"][0]["score"] is None
    finally:
        (webapp.candidates, _, webapp._db, webapp._meta_maps) = orig
        webapp._CAND_CACHE.clear()
        webapp._CAND_CACHE.update(orig[1])
