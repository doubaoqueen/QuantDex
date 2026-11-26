"""D-102 自动补流水回归：删掉账本倒数第二个快照日 → 自动重放 → 与删除前分毫不差。

场景即用户真实事故（11-23 任务未触发）：中间交易日缺快照时，10_daily_run
会先逐日重放缺口、再跑最新日。本测试用 scratch 副本 + 独立报告目录，
不碰真实账本与真实日报。
"""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _bootstrap  # noqa: F401
import pandas as pd

from config import STORE_DIR
from src.data.storage import ParquetStore
from src.live.pipeline import _load_panel, run_pipeline


def _cleanup(tst_db: Path, rep_dir: Path) -> None:
    con = sqlite3.connect(tst_db)
    con.execute("VACUUM")
    con.close()
    Path(tst_db).unlink(missing_ok=True)
    if rep_dir.exists():
        for f in rep_dir.glob("*.md"):
            f.unlink()
        rep_dir.rmdir()
    for b in (STORE_DIR / "sim" / "backups").glob("ledger_autotest_*.sqlite"):
        b.unlink()


def test_auto_backfill_recreates_deleted_day():
    src_db = STORE_DIR / "sim" / "ledger.sqlite"
    tst_db = STORE_DIR / "sim" / "ledger_autotest.sqlite"
    rep_dir = STORE_DIR / "reports" / "autotest_large"
    shutil.copy(src_db, tst_db)

    con = sqlite3.connect(f"file:{tst_db}?mode=ro", uri=True)
    before = pd.read_sql("SELECT date, total_nav, cash, market_value "
                         "FROM account_snapshot ORDER BY date", con)
    con.close()
    last_day = str(before["date"].iloc[-1])
    gap_day = str(before["date"].iloc[-2])       # 复现 11-23 形态：中间缺一天

    con = sqlite3.connect(tst_db)
    for tbl in ("account_snapshot", "positions", "daily_ops"):
        con.execute(f"DELETE FROM {tbl} WHERE date = ?", (gap_day,))
    con.commit()
    con.close()

    try:
        store = ParquetStore()
        panel = _load_panel(store, "000300", with_dividend=False)
        # --- 与 10_daily_run 完全同构的缺口检测 + 逐日重放 ---
        con = sqlite3.connect(f"file:{tst_db}?mode=ro", uri=True)
        have = set(pd.read_sql("SELECT DISTINCT date FROM account_snapshot", con)["date"])
        con.close()
        latest = pd.Timestamp(panel["date"].max())
        assert str(latest.date()) == last_day, "数据末日应等于账本最后快照日"
        floor = pd.Timestamp(min(have))
        miss = sorted(d for d in pd.DatetimeIndex(panel["date"].unique())
                      if floor < d < latest and str(d.date()) not in have)
        assert [str(d.date()) for d in miss] == [gap_day], \
            f"应检出缺口 {gap_day}，实得 {miss}"
        for d in miss:                      # 缺口日逐日重放（面板截断到当日）
            sub = panel[panel["date"] <= d]
            rb = run_pipeline(capital=1_000_000.0, push=False, db_path=tst_db,
                              label="autotest_large", strategy="corrected", _panel=sub)
            assert rb["date"] == str(d.date())
        r = run_pipeline(capital=1_000_000.0, push=False, db_path=tst_db,
                         label="autotest_large", strategy="corrected", _panel=panel)
        assert r["date"] == last_day

        con = sqlite3.connect(f"file:{tst_db}?mode=ro", uri=True)
        after = pd.read_sql("SELECT date, total_nav, cash, market_value "
                            "FROM account_snapshot ORDER BY date", con)
        con.close()
        assert list(after["date"]) == list(before["date"]), "快照日期序列应完全复原"
        for col in ("total_nav", "cash", "market_value"):
            diff = (after[col].astype(float) - before[col].astype(float)).abs().max()
            assert diff < 0.01, f"{col} 复原偏差 {diff}"
    finally:
        _cleanup(tst_db, rep_dir)
