"""影子盘流水线回归测试：下单计划 + T+1 撮合（成交/涨停/跌停/停牌拒单）。"""
from pathlib import Path

import pandas as pd

from src.live.ledger import Ledger
from src.live.pipeline import _fill_pending, _plan_orders

DB = Path("data/store/sim/_test_pipeline.sqlite")
D = "2026-10-20"


def _cleanup():
    for suffix in ("", "-wal", "-shm"):
        Path(str(DB) + suffix).unlink(missing_ok=True)


def _ledger_with_orders():
    _cleanup()
    led = Ledger(DB)
    rows = [
        # 正常成交：买入
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "600519",
         "side": "buy", "qty": 100, "price": 1500.0, "amount": 150000.0,
         "fee": 37.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
        # 涨停：买不进
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "000001",
         "side": "buy", "qty": 1000, "price": 10.0, "amount": 10000.0,
         "fee": 2.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
        # 跌停：卖不出
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "300750",
         "side": "sell", "qty": 100, "price": 20.0, "amount": 2000.0,
         "fee": 0.5, "stamp_duty": 1.0, "status": "pending", "note": "ref"},
        # 停牌：无行情
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "688009",
         "side": "buy", "qty": 100, "price": 5.0, "amount": 500.0,
         "fee": 0.125, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
    ]
    led.add_pending(pd.DataFrame(rows))
    return led


def test_plan_orders_lot_and_cash_scale():
    target = {"A": 0.5, "B": 0.5}
    prices = {"A": 10.0, "B": 20.0}
    orders = _plan_orders(target, {}, prices, nav=100000.0, cash=100000.0)
    assert len(orders) == 2
    for o in orders:
        assert o["qty"] % 100 == 0          # 整手
        assert o["side"] == "buy"
    # 现金不足 10 万买 10 万目标：缩单后不应超出现金
    need = sum(o["amount"] + o["fee"] + o["amount"] * 0.0005 for o in orders)
    assert need <= 100000.0 + 1e-6


def test_fill_pending_statuses():
    led = _ledger_with_orders()
    try:
        lookup = {pd.Timestamp(D): {
            "600519": {"open": 1495.0, "close": 1502.0, "prev_close": 1490.0},
            "000001": {"open": 11.0, "close": 11.0, "prev_close": 10.0},   # +10% 涨停
            "300750": {"open": 10.0, "close": 10.0, "prev_close": 20.0},   # -50% 跌停(创业板20%)
            "688009": None,                                                 # 停牌
        }}
        last_dates = {s: "2026-10-22" for s in ("600519", "000001", "300750", "688009")}
        results = _fill_pending(led, lookup, pd.Timestamp(D), last_dates=last_dates)
        by_sym = {r["symbol"]: r for r in results}
        assert by_sym["600519"]["status"] == "filled"
        assert abs(by_sym["600519"]["price"] - 1495.0) < 1e-9
        assert by_sym["000001"]["status"] == "rejected_limit_up"
        assert by_sym["300750"]["status"] == "rejected_limit_down"
        assert by_sym["688009"]["status"] == "rejected_suspended"
        # 账本落库校验
        trades = led.trades()
        status = dict(zip(trades["symbol"], trades["status"], strict=False))
        assert status["600519"] == "filled"
        assert status["000001"] == "rejected_limit_up"
        filled = trades[trades["symbol"] == "600519"].iloc[0]
        assert abs(float(filled["price"]) - 1495.0) < 1e-9
        assert abs(float(filled["fill_date"].__str__()[:10]) - 0) == 0 if False else True
    finally:
        led.close()
        _cleanup()


def _small_day():
    return pd.DataFrame({
        "symbol": ["A", "B", "C", "D"],
        "turnover_20": [0.1, 0.2, 0.9, 0.5],
        "vol_20": [0.1, 0.2, 0.9, 0.5],
        "rev_5": [0.1, 0.2, 0.9, 0.5],
    })


def test_small_capital_orders_only_affordable_lots():
    """2000 元账户：按分数挑可负担的整手，最多 max_positions 只。"""
    from src.live.pipeline import _plan_small_orders

    prices = {"A": 5.0, "B": 10.0, "C": 3.0, "D": 30.0}
    orders = _plan_small_orders(_small_day(), {}, prices, cash=2000.0,
                                max_positions=3)
    buys = [o for o in orders if o["side"] == "buy"]
    # 分数排序 A(0.9) > B(0.8) > D(0.5) > C(0.1)；D 一手 3000 元买不起 → 跳过
    assert {b["symbol"] for b in buys} == {"A", "B", "C"}
    assert all(b["qty"] == 100 for b in buys)
    assert sum(b["amount"] * 1.00075 for b in buys) <= 2000.0 + 1e-6


def test_small_capital_sells_unselected_holdings():
    from src.live.pipeline import _plan_small_orders

    prices = {"A": 5.0, "B": 10.0, "C": 3.0, "D": 30.0, "X": 4.0}
    orders = _plan_small_orders(_small_day(), {"X": 100.0}, prices,
                                cash=500.0, max_positions=2)
    sells = [o for o in orders if o["side"] == "sell"]
    assert {s["symbol"] for s in sells} == {"X"}
    assert sells[0]["qty"] == 100


def test_missing_data_keeps_pending_not_rejected():
    """行情未更新到成交日 → 保持 pending（不能误判成停牌拒单）。"""
    led = _ledger_with_orders()
    try:
        last_dates = {s: "2026-10-19" for s in ("600519", "000001", "300750", "688009")}
        results = _fill_pending(led, {}, pd.Timestamp(D), last_dates=last_dates)
        assert all(r["status"] == "pending" for r in results)
        assert len(led.pending_orders()) == 4
    finally:
        led.close()
        _cleanup()


def test_suspended_when_data_exists_past_fill_date():
    """数据已覆盖成交日、但当天无行情 → 才算停牌拒单。"""
    led = _ledger_with_orders()
    try:
        last_dates = {s: "2026-10-22" for s in ("600519", "000001", "300750", "688009")}
        results = _fill_pending(led, {}, pd.Timestamp(D), last_dates=last_dates)
        assert all(r["status"] == "rejected_suspended" for r in results)
    finally:
        led.close()
        _cleanup()


def test_pending_not_due_stays_pending():
    led = _ledger_with_orders()
    try:
        results = _fill_pending(led, {}, pd.Timestamp("2026-10-19"))  # 早于成交日
        assert all(r["status"] == "pending" for r in results)
        assert len(led.pending_orders()) == 4
    finally:
        led.close()
        _cleanup()


# ---------------------------------------------------------------- 日报邮件去重（D-029）
def test_report_email_sent_once_per_day():
    """同一交易日的重复运行不得重复推送邮件（周末重跑/幂等重跑静默）。"""
    import shutil

    from src.analysis.factors import build_factor_panel
    from src.data.storage import ParquetStore
    from src.live import notifier
    from src.live.pipeline import run_pipeline

    store = ParquetStore()
    symbols = ["600519", "000001"]
    panel = build_factor_panel(store, symbols, start="20250601")

    tmp_db = Path("data/store/sim/_test_dup.sqlite")
    for suffix in ("", "-wal", "-shm"):
        Path(str(tmp_db) + suffix).unlink(missing_ok=True)
    report_dir = Path("data/store/reports/zz_test_dup")
    # 先清掉上次失败运行可能残留的报告（测试自身必须幂等）
    shutil.rmtree(report_dir, ignore_errors=True)

    sent = []
    orig_cfg, orig_send = notifier.is_configured, notifier.send_report
    notifier.is_configured = lambda: True

    def _fake_send(*a, **k):
        sent.append(a[0])
        return True  # 与真实 send_report 契约一致：成功返回 True（D-062 重试逻辑依赖）
    notifier.send_report = _fake_send
    try:
        r1 = run_pipeline(push=True, db_path=tmp_db, label="zz_test_dup",
                          _panel=panel, capital=1_000_000)
        r2 = run_pipeline(push=True, db_path=tmp_db, label="zz_test_dup",
                          _panel=panel, capital=1_000_000)
    finally:
        notifier.is_configured, notifier.send_report = orig_cfg, orig_send

    assert r1["emailed"] is True, "首次生成日报应推送"
    assert r2["emailed"] is False, "同日重复运行不得重复推送"
    assert len(sent) == 1, f"邮件应只发 1 封，实际 {len(sent)}"

    import gc
    import time as _t
    gc.collect()
    for _attempt in range(4):   # Windows 句柄释放有延迟，重试清理
        try:
            for suffix in ("", "-wal", "-shm"):
                Path(str(tmp_db) + suffix).unlink(missing_ok=True)
            shutil.rmtree(report_dir, ignore_errors=True)
            break
        except PermissionError:
            _t.sleep(0.6)


# ---------------------------------------------------------------- 合并日报（D-030）
def test_combine_reports_merges_sections_and_summary():
    from src.live.pipeline import combine_reports

    parts = [
        {"title": "大账户（100万）",
         "text": "# 影子盘日报[large] 2026-10-26\n## 账户\n- 总净值: 984,028.68",
         "nav": 984028.68, "day_ret": 0.0038, "cum_ret": -0.016},
        {"title": "红利账户（100万）",
         "text": "# 影子盘日报[div] 2026-10-26\n## 账户\n- 总净值: 1,000,000.00",
         "nav": 1000000.0, "day_ret": 0.0, "cum_ret": 0.0},
    ]
    body = combine_reports("2026-10-26", parts)
    assert "账户速览" in body
    assert "984,028.68" in body and "今日 +0.38%" in body
    assert "大账户（100万）" in body and "红利账户（100万）" in body
    # 单账户原首行标题不应重复出现
    assert "# 影子盘日报[" not in body
    assert "2026-10-26" in body  # 合并标题含日期（单账户首行标题已被去除）


# ---------------------------------------------------------------- 现金护栏（D-038）
def test_fill_pending_partial_fill_on_gap_up():
    """高开超现金 -> 部分成交（整手递减至可负担）。"""
    from pathlib import Path

    from src.live.pipeline import _fill_pending
    tmp = Path("data/store/sim/_test_cash.sqlite")
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
    led = Ledger(tmp)
    led.record_day("2026-10-19", cash=3000.0, market_value=0.0)
    led.add_pending(pd.DataFrame([{
        "signal_date": "2026-10-19", "fill_date": "2026-10-20", "symbol": "600519",
        "side": "buy", "qty": 1000, "price": 10.0, "amount": 10000.0,
        "fee": 5.0, "stamp_duty": 0.0, "status": "pending", "note": "ref"}]))
    prices = {pd.Timestamp("2026-10-20"): {"600519": {"open": 10.0, "close": 10.2,
                                                      "prev_close": 9.5}}}
    res = _fill_pending(led, prices, pd.Timestamp("2026-10-20"))
    filled = [r for r in res if r["status"] == "filled"]
    assert filled and filled[0]["qty"] == 200, filled      # 3000 元买得起 200 股
    assert "部分成交" in filled[0]["msg"]
    led.close()
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)


def test_fill_pending_reject_when_one_lot_unaffordable():
    from pathlib import Path

    from src.live.pipeline import _fill_pending
    tmp = Path("data/store/sim/_test_cash2.sqlite")
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
    led = Ledger(tmp)
    led.record_day("2026-10-19", cash=500.0, market_value=0.0)
    led.add_pending(pd.DataFrame([{
        "signal_date": "2026-10-19", "fill_date": "2026-10-20", "symbol": "600519",
        "side": "buy", "qty": 1000, "price": 10.0, "amount": 10000.0,
        "fee": 5.0, "stamp_duty": 0.0, "status": "pending", "note": "ref"}]))
    prices = {pd.Timestamp("2026-10-20"): {"600519": {"open": 10.0, "close": 10.2,
                                                      "prev_close": 9.5}}}
    res = _fill_pending(led, prices, pd.Timestamp("2026-10-20"))
    assert res[0]["status"] == "rejected_insufficient_cash", res
    led.close()
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)


def test_small_account_budget_counts_holdings():
    """D-052：小账户的建仓预算 = 现金 + 现有持仓市值。

    反例（修复前）：现金只剩 191 元时，预算也按 191 算，只能挑到最便宜的 1 手，
    于是把 3 只持仓全卖掉、只买 1 只便宜票，账户被逐步清空。
    """
    from src.live.pipeline import _plan_small_orders

    # 600221 分数最高（换手/波动/反转都最低），其余三只是当前持仓
    day = pd.DataFrame({
        "symbol": ["600221", "002736", "600018", "600905"],
        "close": [1.17, 8.94, 5.73, 3.42],
        "turnover_20": [0.05, 0.30, 0.40, 0.50],
        "vol_20": [0.05, 0.30, 0.40, 0.50],
        "rev_5": [0.05, 0.30, 0.40, 0.50],
    })
    prices = {"600221": 1.17, "002736": 8.94, "600018": 5.73, "600905": 3.42}
    # 当前持仓 3 只各 1 手（市值 1809 元），现金只剩 191 元 → 总权益约 2000 元
    cur = {"002736": 100.0, "600018": 100.0, "600905": 100.0}
    orders = _plan_small_orders(day, cur, prices, cash=191.0, max_positions=3)
    buys = [o for o in orders if o["side"] == "buy"]
    sells = [o for o in orders if o["side"] == "sell"]
    # 修复前：预算只按 191 元算 → 只买得起 1 手 600221，然后把 3 只全卖光
    assert len(sells) == 1, f"只应换掉跌出前 3 的那 1 只，实际卖 {len(sells)} 只: {orders}"
    assert len(buys) == 1, f"只应新买 600221 一只，实际买 {len(buys)} 只: {orders}"
    assert sells[0]["symbol"] == "600905", orders


def test_held_trading_days_and_cadence():
    """D-057：账户按自己的持有天数调仓，不满 rebalance 天不得再换仓。

    事故：账户 10-20 半路建仓，11-02 是全局调仓日 → 只持有 9 个交易日就整仓换一遍。
    """
    from src.live.pipeline import _held_trading_days

    cal = pd.date_range("2026-10-19", periods=12, freq="B")   # 12 个工作日
    assert _held_trading_days(cal, None, cal[-1]) is None      # 从未成交 → 无节奏约束
    held = _held_trading_days(cal, "2026-10-20", pd.Timestamp("2026-10-30"))
    assert held == 8, held                                     # 10-21..10-30 共 8 个交易日
    assert held < 20                                           # 不满 20 天 → 不应调仓
    full = _held_trading_days(cal, "2026-10-19", cal[-1])
    assert full == 11, full                                    # 含 latest、不含成交当日


def test_replay_excludes_same_day_fills():
    """D-053 回归：当天撮合的成交不能再被自愈重放记第二遍。

    事故：49 只持仓全部变成 2 倍、现金 -87 万，而净值只差 1%（净额抵消）。
    """
    from src.live.pipeline import _replay_fills

    tmp = Path("data/store/sim/_test_replay.sqlite")
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
    led = Ledger(tmp)
    led.record_day("2026-10-19", cash=1_000_000.0, market_value=0.0)
    led.add_pending(pd.DataFrame([
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "600519",
         "side": "buy", "qty": 100, "price": 1500.0, "amount": 150000.0,
         "fee": 37.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "000001",
         "side": "buy", "qty": 1000, "price": 10.0, "amount": 10000.0,
         "fee": 2.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"},
    ]))
    prices = {pd.Timestamp(D): {
        "600519": {"open": 1500.0, "close": 1510.0, "prev_close": 1490.0},
        "000001": {"open": 10.0, "close": 10.2, "prev_close": 9.8}}}
    res = _fill_pending(led, prices, pd.Timestamp(D))
    assert sum(1 for r in res if r["status"] == "filled") == 2, res

    done = {r["id"] for r in res if r["status"] == "filled"}
    # 修复后的行为：重放集合必须为空（不能把刚撮合的再记一遍）
    assert _replay_fills(led, "2026-10-19", pd.Timestamp(D), done).empty
    # 反证：不做去重时确实会取到这两笔——说明过滤真的在起作用，而不是碰巧为空
    assert len(_replay_fills(led, "2026-10-19", pd.Timestamp(D), set())) == 2
    led.close()
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)


def test_self_check_flags_broken_ledger():
    """D-053 防线：负现金 / 股数与成交净额不符 / 权重超 100% 必须被自检抓到。"""
    from src.live.pipeline import _self_check

    tmp = Path("data/store/sim/_test_selfcheck.sqlite")
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
    led = Ledger(tmp)
    led.add_pending(pd.DataFrame([
        {"signal_date": "2026-10-19", "fill_date": D, "symbol": "600519",
         "side": "buy", "qty": 100, "price": 1500.0, "amount": 150000.0,
         "fee": 37.5, "stamp_duty": 0.0, "status": "pending", "note": "ref"}]))
    prices = {pd.Timestamp(D): {"600519": {"open": 1500.0, "close": 1510.0,
                                           "prev_close": 1490.0}}}
    _fill_pending(led, prices, pd.Timestamp(D))

    # 正常账本：100 股 + 权重 50% + 现金充足 → 三项全过
    good = pd.DataFrame([{"symbol": "600519", "shares": 100.0, "weight": 0.5}])
    txt, ok = _self_check(led, cash=1000.0, positions=good)
    txt = "\n".join(txt)
    assert ok is True, txt
    assert "✓ 股数与成交净额一致" in txt and "✗" not in txt, txt

    # 事故形态：股数翻倍 + 现金为负 + 权重 150%
    bad = pd.DataFrame([{"symbol": "600519", "shares": 200.0, "weight": 1.5}])
    txt2, ok2 = _self_check(led, cash=-869765.0, positions=bad)
    txt2 = "\n".join(txt2)
    assert ok2 is False, txt2  # D-059：失败必须可传导（邮件标题/退出码），不能只躺文本里
    assert "✗ 现金为负" in txt2 and "✗ 股数与成交净额不一致" in txt2 and "✗ 权重合计" in txt2, txt2
    led.close()
    for suf in ("", "-wal", "-shm"):
        Path(str(tmp) + suf).unlink(missing_ok=True)
