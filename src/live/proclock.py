# -*- coding: utf-8 -*-
"""跨进程文件锁（R3-02）：流水线/更新入口互斥，防双订单与账本竞态。

Windows 用 msvcrt.locking，POSIX 用 fcntl.flock；进程崩溃由 OS 自动释放，
无陈旧锁问题。用法：
    lf = try_lock(path)
    if lf is None:
        print("另一实例在跑"); sys.exit(3)
    ... 干活 ...
    lf.close()   # 关闭即释放
"""
from __future__ import annotations

import os


def try_lock(path):
    """尝试获取独占锁。成功返回文件句柄（保持打开=持锁，close 即释放）；失败返回 None。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None
