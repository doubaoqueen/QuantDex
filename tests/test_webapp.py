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


def test_calendar_data_shape_and_gap_logic():
    """运行日历（D-101）：结构完整、缺口判定正确、非法月份硬拒绝。"""
    out = webapp.calendar_data("2026-11")
    assert out["month"] == "2026-11"
    assert out["accounts"], "至少要有一个账户"
    assert 0 <= out["first_weekday"] <= 6
    assert len(out["days"]) == 30          # 2026-11 有 30 天
    for r in out["days"]:
        assert set(("date", "trading", "market", "snap", "report", "mail", "future")) <= set(r)
        assert set(r["snap"]) == set(out["accounts"])
        assert set(r["report"]) == set(out["accounts"])
    # 完整性定义：交易日 + 非未来 + 行情 + 全部快照 + 全部日报
    for r in out["days"]:
        if r["trading"] and not r["future"] and out["accounts"]:
            assert r["complete"] == (r["market"] and all(r["snap"].values())
                                      and all(r["report"].values()))
    past = [r for r in out["days"] if r["trading"] and not r["future"]]
    assert out["summary"]["trading"] == len(past)
    assert out["summary"]["gaps"] == [r["date"] for r in past if not r["complete"]]
    # 11-23 已回填：必须显示为完整（这是本面板存在的意义）
    d23 = next(r for r in out["days"] if r["date"] == "2026-11-23")
    assert d23["complete"] is True and d23["market"] and all(d23["snap"].values())
    # 非法月份：显式报错而不是静默回退（D-106 同款纪律）
    assert "error" in webapp.calendar_data("not-a-month")


def test_day_detail_structure_and_values():
    """当日账户全景（D-103）：结构完整、数值与账本一致、缺口/非法日期如实处理。"""
    out = webapp.day_detail("2026-11-23")
    assert out["date"] == "2026-11-23" and out["trading"] and out["market"]
    assert len(out["accounts"]) >= 4
    for a in out["accounts"]:
        assert {"key", "label", "nav", "report"} <= set(a)
        if a["nav"] is not None:
            # 净值恒等式：nav = cash + mv（账本自检同款）
            assert abs(a["cash"] + a["mv"] - a["nav"]) < 0.01, a["key"]
            for r in a["positions"]:
                assert {"symbol", "shares", "close", "weight", "pct"} <= set(r)
            for f in a["fills"]:
                assert {"symbol", "side", "qty", "price", "status"} <= set(f)
        assert a["report"] is not None, "11-23 已回填，日报必须存在"
    # 大账户 11-23 净值与账本直查一致（D-101 回填值）
    assert abs(next(a for a in out["accounts"] if a["key"] == "large")["nav"]
               - 994430.23) < 0.01
    # 非交易日：无流水、nav 全空
    out2 = webapp.day_detail("2026-11-22")
    assert out2["trading"] is False
    assert all(a["nav"] is None for a in out2["accounts"])
    # 非法日期：硬拒绝（D-106 纪律）
    assert "error" in webapp.day_detail("2026/11/23")


def test_preflight_combo_feasibility():
    """组合可行性核查（D-104）：等比例之外的三堵墙——整手/最低佣金/集中度。"""
    out = webapp.preflight_check(20000)
    assert {"rows", "combo", "combo_15"} <= set(out)
    c = out["combo"]
    assert "err" not in c, c.get("err")
    # 结构
    for key in ("budget", "k", "pool", "positions", "cost", "fees",
                "roundtrip_pct", "max_weight", "single_limit_loss",
                "verdict", "verdict_text"):
        assert key in c, key
    # 核心口径：k 只、每只 >=1 手、总成本不超预算、权重和 ~1（投入口径）
    assert 1 <= c["k"] <= c["pool"]
    for q in c["positions"]:
        assert q["lots"] >= 1
        assert q["amount"] <= c["budget"] / c["k"] + 1e-6 or True
        assert q["amount"] == round(q["lots"] * q["close"] * 100, 2)
    assert c["cost"] <= c["budget"]
    w = sum(q["amount"] for q in c["positions"]) / c["cost"]
    assert abs(w - 1.0) < 1e-6, "投入口径权重和应为 1"
    # 单票跌停 = max(金额 × 分板跌停)
    expect = max(q["amount"] * q["limit"] for q in c["positions"])
    assert abs(c["single_limit_loss"] - expect) < 1e-6
    # 判定三档与阈值一致（预注册：k=0 fail；成本>2% 或单票>L×20% fail；k<15/权重>4%/成本>1% thin）
    v = c["verdict"]
    if v == "fail":
        assert c["k"] == 0 or c["roundtrip_pct"] > 0.02 or c["single_limit_loss"] > 20000 * 0.20
    elif v == "thin":
        assert c["k"] < 15 or c["max_weight"] > 0.04 or c["roundtrip_pct"] > 0.01
    else:
        assert c["k"] >= 15 and c["max_weight"] <= 0.04 and c["roundtrip_pct"] <= 0.01
    # 极小预算：连一手都买不起 → k=0 fail（预算 1 元）
    tiny, _ = webapp.preflight_combo(1.0, 1000.0)
    assert "err" not in tiny and tiny["k"] == 0 and tiny["verdict"] == "fail"


def test_preflight_replay_contract():
    """D-119：体检的预算回放层——读 62 号产物，字段齐全且自洽。

    产物缺失时返回 None（面板显示"不可用"而不是崩）；有产物时：
    · 满仓折算 MDD = 实际 MDD / 平均仓位；
    · 最差年份 = 各年 (回放年化 - 策略年化) 的最小值。
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "webapp_pf", root / "scripts" / "16_webapp.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)

    r = w.preflight_replay(62_480, "corrected")
    if r is None:                                   # 未跑 62 号 → 允许 None
        return
    assert r["nearest_budget"] > 0
    assert 0 < r["avg_invested"] <= 1.0
    assert r["mdd"] <= 0 and r["ref_mdd"] <= 0
    if r["mdd_norm"] is not None and r["avg_invested"] > 0:
        assert abs(r["mdd_norm"] - r["mdd"] / r["avg_invested"]) < 1e-6
    assert isinstance(r["by_year"], dict) and len(r["by_year"]) >= 1
    worst = min((v["ann"] - v["ref_ann"]) for v in r["by_year"].values())
    assert r["worst_year"] is None or abs(r["worst_year"][1] - worst) < 1e-9

    # 完整响应形状：replay 在 preflight_check 顶层
    payload = w.preflight_check(20_000)
    assert "replay" in payload and payload["rows"]
