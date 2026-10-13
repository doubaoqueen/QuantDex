# -*- coding: utf-8 -*-
"""数据源会话管理：baostock 登录单例、结果转换、统一重试。"""
import time
import pandas as pd
import baostock as bs
import akshare as ak

from config import MAX_RETRIES, RETRY_SLEEP

_BS_LOGGED_IN = False


def ensure_bs_login():
    """确保 baostock 已登录（进程内单例），返回 baostock 模块。"""
    global _BS_LOGGED_IN
    if not _BS_LOGGED_IN:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")
        _BS_LOGGED_IN = True
    return bs


def bs_logout() -> None:
    global _BS_LOGGED_IN
    if _BS_LOGGED_IN:
        bs.logout()
        _BS_LOGGED_IN = False


def bs_to_df(rs) -> pd.DataFrame:
    """baostock 结果集 -> DataFrame。"""
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


def retry_call(fn, *args, label: str = "", **kwargs):
    """带退避的重试调用。"""
    last_err = None
    for i in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(RETRY_SLEEP * (i + 1))
    raise RuntimeError(f"调用失败[{label or getattr(fn, '__name__', '?')}]: {last_err}")
