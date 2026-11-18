"""通达信协议数据源探针（2026-11-18，金太阳通达信内核的协议直连评估）。

背景：用户机器装有国信金太阳专业版 v8.57（D:\\MeineTool\\goldsun，TdxW.exe +
vipdoc + T0002 = 通达信内核实锤）。L1 行情 3 秒一跳、含五档盘口——涨停时买一
挂单量即封单量，正是 D-093 量出的信息墙（封单强度不可观测）的第一块合法踏板。

三条获取路径与本脚本覆盖：
  路径 A 协议直连（pytdx，本脚本）：公网通达信行情节点，实盘时段重试；
  路径 B 本地文件：客户端"盘后数据下载"→ vipdoc/sh/lday/*.day（当前为空，
          需在金太阳里手动点一次下载，之后离线解析，零网络依赖）；
  路径 C GUI 抓取：不推荐（脆弱且慢，明确不做）。
若盘中探针通过：监控器（D-088 后续）可挂第二实时源 + 封单量时序开始积累。

ToS 定位：pytdx 为社区逆向协议，与 akshare 同级（未授权公开接口）；只做研究/
监控数据源不进生产关键路径，带超时与多节点轮换（D-060 纪律原样复用）。

用法：python scripts/net_diag_tdx.py          # 全节点轮测（盘中运行才有完整数据）
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pytdx.hq import TdxHq_API

SERVERS = [("华泰", "180.153.18.170", 7709), ("招商深圳", "119.147.212.81", 7709),
           ("东证通", "218.108.98.244", 7709), ("上海电信", "115.238.90.165", 7709),
           ("方正", "123.129.213.137", 7709), ("同花顺", "60.12.136.31", 7709)]


def probe_one(api, name, host, port) -> bool:
    t0 = time.perf_counter()
    try:
        if not api.connect(host, port, time_out=5):
            print(f"[连接失败] {name}")
            return False
        lat = (time.perf_counter() - t0) * 1000
        q = api.get_security_quotes([(1, "600519"), (0, "000001")])
        b5 = api.get_security_bars(0, 1, "600519", 0, 3)     # 5 分钟
        b1 = api.get_security_bars(7, 1, "600519", 0, 3)     # 1 分钟
        tk = api.get_transaction_data(1, "600519", 3)        # 分笔（L1）
        parts = [f"快照{'✓' if q else '✗'}", f"5分钟{'✓' if b5 else '✗'}",
                 f"1分钟{'✓' if b1 else '✗'}", f"分笔{'✓' if tk else '✗'}"]
        print(f"[{name}] 握手 {lat:.0f}ms | " + " | ".join(parts))
        if q:
            x = q[0]
            print(f"   600519 价 {x['price']:.2f} 昨收 {x['last_close']:.2f} | "
                  f"买一 {x['bid1']}×{x['bid1_vol']} 卖一 {x['ask1']}×{x['ask1_vol']}")
        if b1:
            for b in b1[-2:]:
                print(f"   1min {b['datetime']} C {b['close']:.2f} V {b['vol']}")
        return bool(q or b1)
    except Exception as e:  # noqa: BLE001
        print(f"[异常] {name}: {str(e)[:70]}")
        return False
    finally:
        try:
            api.disconnect()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    api = TdxHq_API()
    ok = 0
    for name, host, port in SERVERS:
        if probe_one(api, name, host, port):
            ok += 1
    print(f"\n可用节点 {ok}/{len(SERVERS)}"
          f"（全空=盘后维护窗口或节点池过时：盘中重试；或金太阳开着时用"
          f" netstat 抓 TdxW 实连节点）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
