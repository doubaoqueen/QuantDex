"""webapp 路由表重构的端到端冒烟（深检 2026-11-23）。

此前 do_GET/do_POST 的 30+ 段 if 链没有任何 HTTP 层测试——路由表重构后，
用真实 Server（随机端口）+ http.client 走一遍：鉴权门 / CSRF 门 / 登录
/ cookie / 表分发 / 404 / 坏参数 400 / 无副作用的 POST 表路径。
"""
import http.client
import importlib.util
import json
import sys
import threading
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "webapp_mod_http", Path(__file__).resolve().parents[1] / "scripts" / "16_webapp.py")
webapp = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    _spec.loader.exec_module(webapp)
finally:
    sys.path.pop(0)


def _start_server():
    srv = webapp.Server(("127.0.0.1", 0), webapp.Handler)   # 端口 0 = 随机空闲端口
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_http_route_table_smoke():
    srv, port = _start_server()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)

        # 未登录：/api/* 一律 401；页面路径回登录页
        conn.request("GET", "/api/overview")
        r = conn.getresponse()
        assert r.status == 401 and json.loads(r.read())["authed"] is False
        conn.request("GET", "/")
        r = conn.getresponse()
        body = r.read().decode("utf-8", "ignore")
        assert r.status == 200 and "QuantDex 控制台" in body

        # CSRF 门：非 JSON Content-Type → 403
        conn.request("POST", "/api/login", body=b"user=x&pass=y",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        assert r.status == 403 and json.loads(r.read())["err"].startswith("forbidden")

        # 错误密码 → 401（1 次失败不触发登录退避；成功后计数清零）
        conn.request("POST", "/api/login", body=json.dumps({"user": "x", "pass": "y"}),
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        assert r.status == 401 and json.loads(r.read())["ok"] is False

        # 正确登录 → 200 + Set-Cookie
        conn.request("POST", "/api/login",
                     body=json.dumps({"user": webapp.AUTH["user"],
                                      "pass": webapp.AUTH["pass"]}),
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        setck = r.getheader("Set-Cookie") or ""
        assert r.status == 200 and "qd_session=" in setck
        tok = setck.split("qd_session=", 1)[1].split(";", 1)[0]
        ck = {"Cookie": f"qd_session={tok}"}

        # 登录后：路由表分发正常（strategies 无数据依赖，纯注册表输出）
        conn.request("GET", "/api/strategies", headers=ck)
        r = conn.getresponse()
        d = json.loads(r.read())
        assert r.status == 200 and "accounts" in d and len(d["accounts"]) >= 1

        # 未知 API → 404；坏参数 → 400（重构前会 500）
        conn.request("GET", "/api/does_not_exist", headers=ck)
        r = conn.getresponse()
        assert r.status == 404 and json.loads(r.read())["err"] == "not found"
        conn.request("GET", "/api/preflight?L=abc", headers=ck)
        r = conn.getresponse()
        assert r.status == 400 and json.loads(r.read())["err"].startswith("参数 L")
        conn.request("GET", "/api/trades?limit=abc&account=no_such_acc", headers=ck)
        r = conn.getresponse()
        assert r.status == 400

        # 带认证 POST：不存在的账户取消订单 → 200 + ok=False（无副作用，覆盖 POST 表分发）
        conn.request("POST", "/api/orders/cancel",
                     body=json.dumps({"account": "no_such_acc", "id": 1}),
                     headers={"Content-Type": "application/json", **ck})
        r = conn.getresponse()
        d = json.loads(r.read())
        assert r.status == 200 and d["ok"] is False and "账户不存在" in d["msg"]

        conn.close()
    finally:
        webapp._LOGIN_FAILS["n"] = 0
        srv.shutdown()
        srv.server_close()
