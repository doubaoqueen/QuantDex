# -*- coding: utf-8 -*-
"""
第 0 步：下载 point-in-time 历史指数成分表并落库（消除幸存者偏差的地基数据）。

数据源：github unliftedq/index-constitution（MIT 许可）
整理自中证指数公司官方调整记录，与 csindex 官网当前成分交叉验证 300/300 一致。
覆盖：沪深300(000300) 自 2005-04-08；中证500(000905) 自 2007-01-15。

用法：python scripts/00_fetch_pit_constituents.py
"""
import io
import tarfile
import _bootstrap  # noqa: F401

import pandas as pd
import requests

from src.data.schema import normalize_code
from src.data.storage import ParquetStore

URL = "https://codeload.github.com/unliftedq/index-constitution/tar.gz/refs/heads/main"
MAPPING = {"000300": "csi300", "000905": "csi500"}


def main() -> None:
    store = ParquetStore()
    print(">> 下载历史成分数据包 ...")
    r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
    r.raise_for_status()
    print(f"   已下载 {len(r.content) / 1024:.0f} KB")

    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
        for index_code, fname in MAPPING.items():
            path = f"index-constitution-main/history/{fname}.csv"
            raw = pd.read_csv(io.BytesIO(tf.extractfile(path).read()))
            out = raw.copy()
            out["symbol"] = out["symbol"].map(normalize_code)
            out["in_date"] = pd.to_datetime(out["opt-in"])
            out["out_date"] = pd.to_datetime(out["opt-out"])
            out.insert(0, "index_code", index_code)
            out = out[["index_code", "symbol", "name", "in_date", "out_date"]]
            out = out.sort_values(["symbol", "in_date"]).reset_index(drop=True)
            store.write(out, "meta", f"pit_constituents_{index_code}",
                        source="github:unliftedq/index-constitution")
            n_mem = out["symbol"].nunique()
            n_adjust = out["in_date"].dropna().nunique()
            print(f"   pit_constituents_{index_code}: {len(out)} 条记录, "
                  f"{n_mem} 只历史成员, {n_adjust} 个纳入时点")


if __name__ == "__main__":
    main()
