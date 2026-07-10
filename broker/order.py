# -*- coding: utf-8 -*-
"""
模拟撮合引擎 - 基础交易对象 (Order & Trade)
"""
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid

class OrderType(Enum):
    """订单类型"""
    MARKET = "MARKET"  # 市价单 (按下一根K线的 Open 加上滑点成交)
    OPPONENT = "OPPONENT"  # 对价单 (tick 下买入吃卖一，卖出吃买一，不额外叠加滑点)
    LIMIT = "LIMIT"    # 限价单/挂单 (指定价格，依靠 K线的高低点穿透来成交)

class Direction(Enum):
    """多空方向"""
    LONG = "LONG"  # 买/做多
    SHORT = "SHORT"  # 卖/做空


class Offset(Enum):
    """开平标志"""
    OPEN = "OPEN"  # 开仓
    CLOSE = "CLOSE"  # 平仓 (默认平昨或优先平昨)
    CLOSE_TODAY = "CLOSE_TODAY"  # 平今仓 (国内期货特有，影响手续费)


class OrderStatus(Enum):
    """订单状态生命周期"""
    PENDING = "PENDING"  # 待撮合 (刚发往交易所，尚未处理)
    FILLED = "FILLED"  # 全部成交，当前的模型不会出现真实情况的部分成交，后续再说
    CANCELED = "CANCELED"  # 已撤销
    REJECTED = "REJECTED"  # 已拒单 (例如资金不足、涨跌停板无法成交)


@dataclass
class Order:
    """
    订单对象 (策略发出的意图)
    """
    symbol: str
    direction: Direction
    offset: Offset
    volume: int
    price: float  # 挂单价 (如果是 0.0 代表市价单)
    insert_time: datetime  # 策略发出信号的时间

    # 运行时状态 (由 Broker 撮合时动态修改)
    order_type: OrderType = OrderType.MARKET
    slippage_ticks: Optional[float] = None
    ttl_seconds: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: int = 0
    filled_price: float = 0.0
    order_id: str = field(default_factory=lambda: f"ORD_{uuid.uuid4().hex[:8]}")


@dataclass
class Trade:
    """
    成交单对象 (Broker 撮合成功后生成的真实物理结果)
    """
    symbol: str
    direction: Direction
    offset: Offset
    volume: int
    price: float  # 包含滑点后的真实成交价
    trade_time: datetime  # 真实成交的物理时间 (通常是发单时间后的下一根 K 线)
    commission: float  # 扣除的真实手续费
    slippage_cost: float  # 这笔交易被滑点吃掉的隐含成本 (仅做统计归因用)
    order_id: str  # 关联的原始订单 ID
    trade_id: str = field(default_factory=lambda: f"TRD_{uuid.uuid4().hex[:8]}")
    is_rollover: bool = False  # 是否为换月产生的成交
    contract_symbol: str = ""  # 实际成交合约；普通主连交易可为空
    roll_from_contract: str = ""  # 换月平仓对应的旧合约
    roll_to_contract: str = ""  # 换月开仓对应的新合约
