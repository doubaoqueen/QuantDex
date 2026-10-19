# -*- coding: utf-8 -*-
"""数据源会话管理：baostock 登录单例、结果转换、统一重试。"""
import threading
import time
import pandas as pd
import baostock as bs
import akshare as ak

from config import MAX_RETRIES, RETRY_SLEEP

_BS_LOGGED_IN = False
# baostock 是单连接串行协议：查询与读结果必须原子地占用连接，多线程
# 并发会交错收发、损坏数据流（表现为 utf-8 解码错误 / "接收数据异常"）。
# 所有 baostock 调用必须经此锁串行化。
_bs_lock = threading.RLock()


def ensure_bs_login():
    """确保 baostock 已登录（进程内单例，多线程安全），返回 baostock 模块。"""
    global _BS_LOGGED_IN
    with _bs_lock:
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


def bs_query(fn, *args, **kwargs) -> pd.DataFrame:
    """串行化执行一次 baostock 查询并转 DataFrame。

    baostock 查询与读结果必须在同一把锁内完成（批处理线程池场景下
    严禁直接调用 bs.query_* 再 bs_to_df）。"""
    with _bs_lock:
        rs = fn(*args, **kwargs)
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 查询失败: {rs.error_code} {rs.error_msg}")
        return bs_to_df(rs)


# 确定性失败（重试无意义）：数据源明确无数据/代码不存在/已退市/已下市。
# 命中这些特征立即抛出，不为退市股白等 3 次退避（约 10 秒）。
# 网络抖动（连接重置、超时、限流）不在此列，仍正常重试。
_FATAL_KEYWORDS = ("no value to decode", "no data", "数据为空", "empty",
                   "不存在该代码", "已退市", "已下市", "无此代码")


def retry_call(fn, *args, label: str = "", fatal_keywords=_FATAL_KEYWORDS, **kwargs):
    """带退避的重试调用；确定性失败（无数据/退市）立即抛出，不重试。"""
    last_err = None
    for i in range(MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            if any(k in msg for k in fatal_keywords):
                break
            time.sleep(RETRY_SLEEP * (i + 1))
    raise RuntimeError(f"调用失败[{label or getattr(fn, '__name__', '?')}]: {last_err}")
