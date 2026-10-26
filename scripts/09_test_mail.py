"""
邮件推送连通性测试：发送一封测试邮件。
运行: python scripts/09_test_mail.py
配置: .env（SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/REPORT_TO，已被 gitignore）
"""
import datetime

import _bootstrap  # noqa: F401

from src.live.notifier import config, is_configured, send_report


def main() -> None:
    c = config()
    print(f"SMTP: {c['host']}:{c['port']} | 发件: {c['user'] or '(未配置)'} "
          f"| 收件: {c['to'] or '(未配置)'} | 授权码: {'已配置' if c['password'] else '缺失'}")
    if not is_configured():
        print("未配置完整（检查 .env 中 SMTP_USER / SMTP_PASS / REPORT_TO 是否为真实邮箱）")
        return
    body = (f"这是 QuantDex 邮件推送连通性测试。\n"
            f"时间: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"收到说明 SMTP 配置正确，影子盘日报会自动发到这里。")
    ok = send_report("QuantDex 测试邮件", body)
    print("结果:", "成功" if ok else "失败")


if __name__ == "__main__":
    main()
