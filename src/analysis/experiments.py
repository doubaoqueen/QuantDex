"""实验存档（Recorder 轻量版，对标 qlib Recorder/MLflow 思想，D-031）。

把"参数 ↔ 结果指标 ↔ 产物路径 ↔ 代码版本"绑成一条 JSON 记录，
落 data/store/experiments/，可随时 list 对比。零第三方依赖。

用法：
    from src.analysis.experiments import record, list_experiments
    record("portfolio_backtest",
           params={"top_n": 50, "rebalance": 20, "strategy": "topkdrop"},
           metrics={"总收益率": 0.74, "最大回撤": -0.26},
           notes="说明", artifacts=["portfolio_results/nav_x.parquet"])
    list_experiments("portfolio_backtest")   # 历史对比
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
from pathlib import Path

import pandas as pd

from config import STORE_DIR

EXP_DIR = STORE_DIR / "experiments"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent, text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


def record(name: str, params: dict, metrics: dict,
           notes: str = "", artifacts: list[str] | None = None) -> Path:
    """写一条实验记录（JSON），返回文件路径。任何字段须可 JSON 序列化。"""
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now()
    rec = {
        "id": ts.strftime("%Y%m%d_%H%M%S"),
        "name": name,
        "params": params,
        "metrics": metrics,
        "notes": notes,
        "artifacts": artifacts or [],
        "git_commit": _git_commit(),
        "created_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = EXP_DIR / f"{rec['id']}_{name}.json"
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[experiments] 已存档: {p.name}", flush=True)
    return p


def list_experiments(name: str | None = None) -> pd.DataFrame:
    """列出实验（可按名字过滤），按时间倒序；无记录返回空表。"""
    if not EXP_DIR.exists():
        return pd.DataFrame()
    rows = []
    for p in sorted(EXP_DIR.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if name and rec.get("name") != name:
            continue
        rows.append({"id": rec["id"], "name": rec["name"],
                     "params": rec.get("params"), "metrics": rec.get("metrics"),
                     "notes": rec.get("notes", ""),
                     "git": rec.get("git_commit", ""),
                     "created_at": rec.get("created_at")})
    return pd.DataFrame(rows).sort_values("created_at", ascending=False)
