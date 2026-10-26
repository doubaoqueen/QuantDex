"""网络/数据源诊断: 东财 vs 新浪 vs baostock, 以及代理环境。
运行: python scripts/_net_diag.py     (结果自动写入 net_diag_result.txt)
"""
import os
import sys

import requests

OUT = []
def log(s=""):
    OUT.append(str(s))
    print(s, flush=True)

def t(name, fn):
    try:
        r = fn()
        log(f"OK  {name}: {r}")
    except Exception as e:
        log(f"ERR {name}: {type(e).__name__} {str(e)[:120]}")

log("== 代理环境 ==")
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "NO_PROXY", "no_proxy"):
    v = os.environ.get(k)
    if v:
        log(f"  {k} = {v}")
log("  (以上为空 = 无代理环境变量)")

log()
log("== 基础连通性 ==")
t("东财 push2his", lambda: requests.get(
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    params={"secid": "1.600519", "klt": "101", "fqt": "1",
            "beg": "20240101", "end": "20240110"},
    timeout=15).status_code)
t("新浪 行情", lambda: requests.get(
    "https://hq.sinajs.cn/list=sh600519",
    headers={"Referer": "https://finance.sina.com.cn"}, timeout=15).status_code)

log()
log("== akshare 东财日线 ==")
t("stock_zh_a_hist(600519)", lambda: __import__("akshare").stock_zh_a_hist(
    symbol="600519", period="daily", start_date="20240101", end_date="20240110",
    adjust="qfq").shape)

log()
log("== akshare 新浪日线 ==")
t("stock_zh_a_daily(sh600519)", lambda: __import__("akshare").stock_zh_a_daily(
    symbol="sh600519", start_date="20240101", end_date="20240110",
    adjust="qfq").shape)

log()
log("== baostock ==")
import baostock as bs

lg = bs.login()
log(f"  login: {lg.error_code} {lg.error_msg}")

def bs_query(code):
    rs = bs.query_history_k_data_plus(code, "date,close",
                                      start_date="2024-01-01",
                                      end_date="2024-01-10", frequency="d")
    if rs.error_code != "0":
        raise RuntimeError(f"err={rs.error_code} {rs.error_msg}")
    import pandas as pd
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields).shape

t("指数查询 sh.000300", lambda: bs_query("sh.000300"))
t("个股查询 sh.600519", lambda: bs_query("sh.600519"))
bs.logout()

with open("net_diag_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(OUT))
log()
log("done -> net_diag_result.txt")
