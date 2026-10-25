# -*- coding: utf-8 -*-
"""
数据备份（D-035：数据出 git 后的唯一异地/本地备份手段）。

打包 data/store（排除可重建的 per-symbol 缓存 market/daily/ 与旧账本备份 sim/backups/）
为 zip，存 backups/。建议节奏：每月一次 + 大动作（迁移/清理）前一次。

用法：
    python scripts/23_backup_data.py            # 本地打包
    python scripts/23_backup_data.py --upload   # 打包后尝试用 gh 上传到 GitHub Release（需 gh 已登录）
"""
import datetime as _dt
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import _bootstrap  # noqa: F401
from config import STORE_DIR

ROOT = STORE_DIR.parent.parent          # 项目根
OUT_DIR = ROOT / "backups"
SKIP_PREFIXES = ("market/daily/", "sim/backups/", "logs/")


def main() -> None:
    upload = "--upload" in sys.argv
    OUT_DIR.mkdir(exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M")
    out = OUT_DIR / f"quantdex_data_{stamp}.zip"
    n, total = 0, 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(STORE_DIR.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(STORE_DIR).as_posix()
            if rel.startswith(SKIP_PREFIXES):
                continue
            z.write(p, f"data/store/{rel}")
            n += 1
            total += p.stat().st_size
    print(f">> 备份完成: {out.name}")
    print(f"   文件 {n} 个 | 原始 {total / 1e6:.0f} MB | 压缩后 {out.stat().st_size / 1e6:.0f} MB")

    # 保留最近 6 份
    olds = sorted(OUT_DIR.glob("quantdex_data_*.zip"))
    for old in olds[:-6]:
        old.unlink(missing_ok=True)

    if upload:
        if shutil.which("gh") is None:
            print(">> 未安装 gh CLI，跳过上传（备份仅在本地 backups/）")
            return
        tag = f"data-backup-{stamp}"
        try:
            subprocess.run(["gh", "release", "create", tag, str(out),
                            "--title", f"数据备份 {stamp}",
                            "--notes", "quantdex_data 备份（数据已出 git，见 D-035）"],
                           check=True, cwd=str(ROOT))
            print(f">> 已上传 GitHub Release: {tag}")
        except subprocess.CalledProcessError as e:
            print(f">> 上传失败（备份仍在本地）: {e}")


if __name__ == "__main__":
    main()
