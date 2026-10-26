"""GitHub 连通性诊断: DNS / TCP / HTTPS / git 代理 / ssh, 结果写入 gh_diag_result.txt
运行: python scripts/net_diag_github.py
"""
import os
import socket
import subprocess
import sys

OUT = []

def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

def sh(args, timeout=15):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        return (p.returncode, (p.stdout or "").strip()[:200], (p.stderr or "").strip()[:200])
    except Exception as e:  # noqa: BLE001
        return (-1, "", f"{type(e).__name__}: {e}")

log("== 1. 代理环境变量 ==")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
    v = os.environ.get(k)
    log(f"  {k} = {v if v else '(无)'}")

log()
log("== 2. DNS 解析 github.com ==")
try:
    infos = socket.getaddrinfo("github.com", 443, proto=socket.IPPROTO_TCP)
    ips = sorted({i[4][0] for i in infos})
    log(f"  IPs: {ips}")
except Exception as e:  # noqa: BLE001
    log(f"  DNS 失败: {type(e).__name__}: {e}")

log()
log("== 3. TCP 443 连接 ==")
for host in ("github.com", "codeload.github.com", "api.github.com"):
    try:
        s = socket.create_connection((host, 443), timeout=5)
        s.close()
        log(f"  {host}:443 OK")
    except Exception as e:  # noqa: BLE001
        log(f"  {host}:443 FAIL {type(e).__name__}: {e}")

log()
log("== 4. HTTPS 请求 ==")
try:
    import requests
    for url in ("https://github.com", "https://codeload.github.com"):
        try:
            r = requests.head(url, timeout=8)
            log(f"  {url}: HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            log(f"  {url}: FAIL {type(e).__name__}: {str(e)[:120]}")
except ImportError:
    log("  (无 requests)")

log()
log("== 5. git 代理配置 ==")
for key in ("http.proxy", "https.proxy"):
    rc, out, err = sh(["git", "config", "--get", key])
    log(f"  {key} = {out if rc == 0 else '(未配置)'}")

log()
log("== 6. git ls-remote (真实 push 通道测试) ==")
rc, out, err = sh(["git", "ls-remote", "https://github.com/doubaoqueen/QuantDex.git", "HEAD"], timeout=20)
log(f"  rc={rc} out={out[:120] if out else ''} err={err[:200] if err else ''}")

log()
log("== 7. SSH 通道 ==")
ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
keys = [f for f in os.listdir(ssh_dir)] if os.path.isdir(ssh_dir) else []
log(f"  ~/.ssh 内容: {keys if keys else '(不存在/为空)'}")
rc, out, err = sh(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8",
                   "-T", "git@github.com"], timeout=15)
log(f"  ssh -T git@github.com: rc={rc} {out[:120] if out else ''} {err[:120] if err else ''}")

with open("gh_diag_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(OUT))
log()
log("done -> gh_diag_result.txt")
