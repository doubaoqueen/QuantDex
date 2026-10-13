import baostock as bs
import pandas as pd
from pathlib import Path

def fetch_510300():
    lg = bs.login()
    if lg.error_code != '0':
        print("登录失败:", lg.error_msg)
        return

    rs = bs.query_history_k_data_plus(
        "sh.510300",
        "date,code,open,high,low,close,volume,amount,adjustflag",
        start_date="2020-01-01",
        end_date="2024-12-31",
        frequency="d",
        adjustflag="2"
    )

    rows = []
    while rs.error_code == '0' and rs.next():
        rows.append(rs.get_row_data())

    df = pd.DataFrame(rows, columns=rs.fields)
    bs.logout()

    out = Path("data/raw/510300.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(df.head())
    print(f"已保存到 {out}")

if __name__ == "__main__":
    fetch_510300()