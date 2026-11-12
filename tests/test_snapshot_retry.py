"""东财快照长间隔重试回归（2026-11-13）：瞬时断连不应直接触发 baostock 全量兜底。"""
import importlib.util
import types
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "snapshot_mod", Path(__file__).resolve().parents[1] / "src" / "data" / "snapshot.py")
snapshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapshot)


def _fake_ak(script):
    """按脚本逐次返回/抛错的 akshare 桩；脚本耗尽后重复最后一项。"""
    state = {"calls": 0}

    class _AK:
        def stock_zh_a_spot_em(self):
            action = script[min(state["calls"], len(script) - 1)]
            state["calls"] += 1
            if isinstance(action, Exception):
                raise action
            return action

    return _AK(), state


def _patch_ak(fake):
    """替换 src.data.sources.ak（fetch_spot 在函数体内延迟 import，走命名空间查找）。"""
    import src.data.sources as sources
    orig = sources.ak
    sources.ak = fake
    return lambda: setattr(sources, "ak", orig)


def _patch_sleep():
    """把 snapshot 命名空间里的 time 换成桩（不动全局 time 模块）。返回 (恢复函数, 睡眠计数)。"""
    state = {"sleeps": 0}
    orig = snapshot.time
    snapshot.time = types.SimpleNamespace(
        sleep=lambda s: state.__setitem__("sleeps", state["sleeps"] + 1))
    return (lambda: setattr(snapshot, "time", orig)), state


def test_spot_retry_recovers_on_transient_failure():
    df = pd.DataFrame({"a": [1]})
    fake, ak_st = _fake_ak([ConnectionError("Remote end closed"),
                            ConnectionResetError("reset"), df])
    undo_ak = _patch_ak(fake)
    undo_sleep, sleep_st = _patch_sleep()
    try:
        out = snapshot.fetch_spot()
    finally:
        undo_ak()
        undo_sleep()
    assert out is df
    assert ak_st["calls"] == 3
    assert sleep_st["sleeps"] == 2


def test_spot_retry_gives_up_after_max_attempts():
    fake, ak_st = _fake_ak([ConnectionError("down")])
    undo_ak = _patch_ak(fake)
    undo_sleep, sleep_st = _patch_sleep()
    try:
        try:
            snapshot.fetch_spot()
        except RuntimeError as e:
            assert "重试" in str(e) and "仍失败" in str(e)
        else:
            raise AssertionError("应抛 RuntimeError 而不是静默返回")
    finally:
        undo_ak()
        undo_sleep()
    assert ak_st["calls"] == snapshot._SPOT_RETRY_ATTEMPTS
    assert sleep_st["sleeps"] == snapshot._SPOT_RETRY_ATTEMPTS - 1


def test_spot_retry_empty_dataframe_retried():
    df = pd.DataFrame({"a": [1]})
    fake, ak_st = _fake_ak([None, df])          # 空结果也视同失败进入重试
    undo_ak = _patch_ak(fake)
    undo_sleep, sleep_st = _patch_sleep()
    try:
        out = snapshot.fetch_spot()
    finally:
        undo_ak()
        undo_sleep()
    assert out is df
    assert ak_st["calls"] == 2
    assert sleep_st["sleeps"] == 1


def test_spot_retry_attempt_override_for_test_and_callers():
    df = pd.DataFrame({"a": [1]})
    fake, ak_st = _fake_ak([ConnectionError("x"), df])
    undo_ak = _patch_ak(fake)
    undo_sleep, sleep_st = _patch_sleep()
    try:
        out = snapshot.fetch_spot(max_attempts=2, delay_s=0.0)
    finally:
        undo_ak()
        undo_sleep()
    assert out is df
    assert ak_st["calls"] == 2
    assert sleep_st["sleeps"] == 1
