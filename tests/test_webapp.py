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
