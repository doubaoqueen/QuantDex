"""按日分区测试：一天一个文件、按 symbol+adjust 去重、批次缓冲。"""
import pathlib
import shutil

import pandas as pd

from src.data import partitions

BASE = pathlib.Path("data/store/_test_partitions")


def _fresh():
    shutil.rmtree(BASE, ignore_errors=True)
    BASE.mkdir(parents=True, exist_ok=True)


def test_write_days_one_file_per_day():
    old = partitions.PART_DIR
    _fresh()
    partitions.PART_DIR = BASE
    try:
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-10-22", "2026-10-22", "2026-10-23"]),
            "symbol": ["600519", "600519", "000001"],
            "close": [1500.0, 1501.0, 10.0],
        })
        n = partitions.write_days(df, adjust="qfq")
        assert n == 2
        d = pd.read_parquet(BASE / "2026-10-22.parquet")
        assert len(d) == 1
        assert float(d["close"].iloc[0]) == 1501.0
        assert (BASE / "2026-10-23.parquet").exists()
    finally:
        partitions.PART_DIR = old
        shutil.rmtree(BASE, ignore_errors=True)


def test_batch_queue_flushes():
    old = partitions.PART_DIR
    _fresh()
    partitions.PART_DIR = BASE
    try:
        class FakeStore:
            _reg_suspend = True
            _daily_delta = None

        s = FakeStore()
        partitions.queue_daily(s, pd.DataFrame({
            "date": pd.to_datetime(["2026-10-22"]),
            "symbol": ["600519"], "close": [1.0]}), "qfq")
        assert not (BASE / "2026-10-22.parquet").exists()
        s._reg_suspend = False
        partitions.flush_queue(s)
        assert (BASE / "2026-10-22.parquet").exists()
    finally:
        partitions.PART_DIR = old
        shutil.rmtree(BASE, ignore_errors=True)
