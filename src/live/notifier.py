"""
邮件推送（SMTP，QQ 邮箱为例）。

配置来源优先级：环境变量 > 项目根 .env（.env 已被 .gitignore 忽略，**绝不要提交**）：
    SMTP_HOST   默认 smtp.qq.com
    SMTP_PORT   默认 465（SSL）
    SMTP_USER   发件邮箱（如 123456789@qq.com）
    SMTP_PASS   邮箱授权码（不是登录密码）
    REPORT_TO   收件地址（默认 = SMTP_USER）

用法：
    from src.live.notifier import is_configured, send_report
    send_report("影子盘日报 2026-10-20", "正文（纯文本/Markdown）")
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


def _load_env_file() -> dict:
    vals: dict[str, str] = {}
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key) or _load_env_file().get(key) or default


def config() -> dict:
    return {
        "host": _get("SMTP_HOST", "smtp.qq.com"),
        "port": int(_get("SMTP_PORT", "465")),
        "user": _get("SMTP_USER"),
        "password": _get("SMTP_PASS"),
        "to": _get("REPORT_TO") or _get("SMTP_USER"),
    }


def is_configured() -> bool:
    """已配置且邮箱格式合法（排除占位符）才视为可发送。"""
    c = config()
    u, t = c["user"], c["to"]
    return bool(u and "@" in u and "你的" not in u
                and t and "@" in t and "你的" not in t
                and c["password"])


def send_report(subject: str, body: str, to: str | None = None) -> bool:
    """发送纯文本邮件；未配置或失败返回 False（不影响主流程）。"""
    c = config()
    if not (c["user"] and c["password"]):
        print("[notifier] 未配置 SMTP_USER/SMTP_PASS，跳过邮件推送")
        return False
    to = to or c["to"]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = c["user"]
    msg["To"] = to
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(c["host"], c["port"], context=ctx, timeout=20) as s:
            s.login(c["user"], c["password"])
            s.sendmail(c["user"], [to], msg.as_string())
        print(f"[notifier] 邮件已发送 -> {to}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[notifier] 邮件发送失败: {type(e).__name__}: {e}")
        return False
