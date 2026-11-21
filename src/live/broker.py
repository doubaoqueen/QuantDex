"""BrokerAdapter：从模拟到实践的执行层接口（D-104，2026-11-21）。

背景：外部体检 + 四周归因（D-098/099）确认执行基建健康，QMT 资金门槛
（10 万档）成为从模拟到实践的最后前置。本模块把"执行"抽象成接口：
  - ShadowBroker：现行影子盘口径（Ledger pending → T+1 开盘撮合）——**默认实现，
    行为与 run_pipeline 现状完全一致**（run_pipeline 的重构拆分属 Tier-2 捆绑项，
    本模块只立契约，不改现有链路）；
  - QmtBroker：xtquant/xttrader 实盘骨架——资金门槛满足后启用，当前所有执行
    方法显式抛 NotImplementedError（防止未完成实现被误调用）。

QMT 预研笔记（2026-11-19/20 探针，D-102）：
  - 依赖链：xtquant(Python) → QMT 客户端（本机常驻，极简版/投研版）→ 券商服务；
    客户端不在线则 xtdata/xttrader 全部不可用（实测）。
  - 门槛：各券商不同（常见 10 万档），需向国信客户经理核实开通条件与 miniQMT
    是否支持 Python 外挂。
  - 行情升级：xtdata 实时 L1 含五档——监控器（D-089）的东财/新浪双源可迁移，
    东财间歇断连问题终结。
  - 交易 API：xttrader.order_stock（异步下单 + 回报回调）；资金账号与 QMT 客户端
    同源。下单位置 = run_pipeline 的目标权重输出之后（BrokerAdapter 的 qmt 实现）。

红线（D-009/D-102）：资金到位后 10 万门槛资金 ≠ 10 万策略资金——L=2 万约束下
corrected 最大可投入 6.4 万，其余为门槛垫与现金缓冲；真实交易第一步 =
2000 元真实实盘先跑一个月验证执行链与真钱心理，再逐步放大到风险预算上限。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BrokerAdapter(ABC):
    """执行层契约：目标权重/订单 → 账本与回报。shadow 与 qmt 各给一个实现。"""

    name: str = "adapter"
    real_money: bool = False

    @abstractmethod
    def submit_orders(self, orders: list[dict]) -> list[dict]:
        """提交订单（含 symbol/side/qty/price/fill_date 等），返回受理结果列表。

        shadow 实现：写入 Ledger pending（fill_date=次一交易日），由日更链在
        次日开盘撮合（run_pipeline 现行口径）；qmt 实现：xttrader 同步/异步下单，
        回报落 trade_log。
        """

    @abstractmethod
    def account_snapshot(self) -> dict:
        """账户状态：cash / positions / total_nav（体检面板与日报共用）。"""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单（人工否决走这里；shadow = Ledger 状态翻转，qmt = xttrader 撤单）。"""

    def capabilities(self) -> dict:
        """能力声明：real_money / 实时五档 / 可撤单时段等，供面板与守卫读取。"""
        return {"real_money": self.real_money}


class ShadowBroker(BrokerAdapter):
    """影子盘实现：现行 Ledger pending → T+1 开盘撮合（零行为变更）。"""

    name = "shadow"
    real_money = False

    def __init__(self, ledger) -> None:
        self.led = ledger

    def submit_orders(self, orders: list[dict]) -> list[dict]:
        out = []
        for o in orders:
            self.led.add_order(o["symbol"], o["side"], o["qty"], o.get("price"),
                               fill_date=o.get("fill_date"))
            out.append({**o, "status": "pending"})
        return out

    def account_snapshot(self) -> dict:
        return self.led.account_snapshot()

    def cancel_order(self, order_id: str) -> bool:
        return self.led.cancel_order(order_id)


class QmtBroker(BrokerAdapter):
    """QMT 实盘骨架：xtquant/xttrader 调用点已定位，资金门槛满足后启用。

    启用前置（全部满足才允许 real_money=True）：
    ① 券商 QMT 权限开通（门槛咨询国信）；② 2000 元真实实盘一个月（执行链与
    真钱心理验证，D-102 纪律延伸）；③ D-010 alpha 裁决（12-29 归因）非负面。
    """

    name = "qmt"
    real_money = True

    def submit_orders(self, orders: list[dict]) -> list[dict]:
        raise NotImplementedError("QMT 权限开通 + 实盘灰度完成后启用（见 docstring 前置清单）")

    def account_snapshot(self) -> dict:
        raise NotImplementedError("QMT 权限开通后启用：xttrader.query_account")

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("QMT 权限开通后启用：xttrader.cancel_stock_order")


def get_broker(kind: str = "shadow", ledger=None) -> BrokerAdapter:
    """工厂：kind ∈ {shadow, qmt}。守卫读 capabilities()["real_money"] 决定放行。"""
    if kind == "shadow":
        return ShadowBroker(ledger)
    if kind == "qmt":
        return QmtBroker()
    raise ValueError(f"未知 broker 类型：{kind}")
