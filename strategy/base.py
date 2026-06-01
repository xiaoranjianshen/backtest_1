# -*- coding: utf-8 -*-
"""
策略基类 (Base Strategy)
作用：定义策略生命周期，封装极简交易 API
"""
from datetime import datetime
from broker.order import Order, Direction, Offset, OrderType
from broker.match_engine import MatchEngine
from portfolio.account import Account


class BaseStrategy:
    def __init__(self, broker: MatchEngine, account: Account, symbol: str):
        """
        :param broker: 绑定的撮合引擎
        :param account: 绑定的资金账户
        :param symbol: 该策略主做的交易品种 (例如 'rb')
        """
        self.broker = broker
        self.account = account
        self.symbol = symbol.lower()

        # 记录策略是否已经初始化
        self.inited = False

    def on_init(self):
        """
        策略初始化钩子：
        在这里计算历史数据的指标（比如算好前20天的均线）
        """
        pass

    def on_bar(self, current_time: datetime, bar_data: dict):
        """
        💥 核心驱动：每分钟/每天 K 线到来时触发！
        :param current_time: 当前时间戳
        :param bar_data: 当前全市场切片数据 {'rb': {'open': 3500, 'close': 3510...}}
        """
        raise NotImplementedError("策略必须实现 on_bar 方法！")

    # =========================================================
    # 极简交易 API (Syntactic Sugar 语法糖)
    # 策略研究员不需要去组装复杂的 Order 对象，直接调这四个方法即可
    # =========================================================

    def buy(self, volume: int, price: float = 0.0, reference_price: float = None):
        """开多 (Open Long)"""
        order_type = OrderType.MARKET if price == 0.0 else OrderType.LIMIT
        ref_price = reference_price if reference_price else price
        order = Order(
            symbol=self.symbol, direction=Direction.LONG, offset=Offset.OPEN,
            volume=volume, price=price, order_type=order_type, insert_time=datetime.now()
        )
        self.broker.insert_order(order, reference_price=ref_price)

    def sell(self, volume: int, price: float = 0.0, reference_price: float = None):
        """平多 (Close Long)"""
        order_type = OrderType.MARKET if price == 0.0 else OrderType.LIMIT
        ref_price = reference_price if reference_price else price
        order = Order(
            symbol=self.symbol, direction=Direction.SHORT, offset=Offset.CLOSE,
            volume=volume, price=price, order_type=order_type, insert_time=datetime.now()
        )
        self.broker.insert_order(order, reference_price=ref_price)

    def short(self, volume: int, price: float = 0.0, reference_price: float = None):
        """开空 (Open Short)"""
        order_type = OrderType.MARKET if price == 0.0 else OrderType.LIMIT
        ref_price = reference_price if reference_price else price
        order = Order(
            symbol=self.symbol, direction=Direction.SHORT, offset=Offset.OPEN,
            volume=volume, price=price, order_type=order_type, insert_time=datetime.now()
        )
        self.broker.insert_order(order, reference_price=ref_price)

    def cover(self, volume: int, price: float = 0.0, reference_price: float = None):
        """平空 (Close Short)"""
        order_type = OrderType.MARKET if price == 0.0 else OrderType.LIMIT
        ref_price = reference_price if reference_price else price
        order = Order(
            symbol=self.symbol, direction=Direction.LONG, offset=Offset.CLOSE,
            volume=volume, price=price, order_type=order_type, insert_time=datetime.now()
        )
        self.broker.insert_order(order, reference_price=ref_price)

    def cancel_all(self):
        """撤销当前品种的所有未成交挂单"""
        # (后续扩展)
        pass