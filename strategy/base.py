# -*- coding: utf-8 -*-
"""
策略基类 (Base Strategy)
作用：提供订单 API 和账户查询能力。

新策略应优先继承 strategy.general_template.GeneralSignalStrategy。
"""
from datetime import datetime
from typing import List, Union

from broker.order import Order, Direction, Offset, OrderType
from broker.match_engine import MatchEngine
from portfolio.account import Account


class BaseStrategy:
    def __init__(self, broker: MatchEngine, account: Account, symbols: Union[str, List[str]]):
        self.broker = broker
        self.account = account
        if isinstance(symbols, str):
            self.symbols = [symbols.lower()]
        else:
            self.symbols = [sym.lower() for sym in symbols]
        self.inited = False
        self.current_time = None

    def on_init(self):
        pass

    def on_bar(self, current_time: datetime, bar_data: dict):
        self.current_time = current_time
        raise NotImplementedError("策略必须实现 on_bar 方法！")

    def get_position_volume(self, symbol: str, direction: Direction) -> int:
        """Return current physical position volume for one symbol and direction."""
        from config import pure_product_code
        raw_code = pure_product_code(symbol)
        pos_key = self.account._get_position_key(raw_code, direction)
        pos = self.account.positions.get(pos_key)
        return self.account._position_volume(pos) if pos else 0

    def get_net_position(self, symbol: str) -> int:
        """Net position convention: long lots are positive, short lots are negative."""
        long_vol = self.get_position_volume(symbol, Direction.LONG)
        short_vol = self.get_position_volume(symbol, Direction.SHORT)
        return long_vol - short_vol

    @staticmethod
    def _order_type_from_price(price: float, order_type=None):
        if order_type is not None:
            if isinstance(order_type, str):
                return OrderType(order_type.strip().upper())
            return order_type
        return OrderType.MARKET if price == 0.0 else OrderType.LIMIT

    def buy(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None,
            slippage_ticks: float = None, order_type=None, ttl_seconds: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price is not None else price
        order = Order(symbol=symbol, direction=Direction.LONG, offset=Offset.OPEN,
                      volume=volume, price=price, order_type=self._order_type_from_price(price, order_type),
                      insert_time=self.current_time, slippage_ticks=slippage_ticks, ttl_seconds=ttl_seconds)
        self.broker.insert_order(order, reference_price=ref_price)

    def sell(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None,
             slippage_ticks: float = None, order_type=None, ttl_seconds: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price is not None else price
        order = Order(symbol=symbol, direction=Direction.SHORT, offset=Offset.CLOSE,
                      volume=volume, price=price, order_type=self._order_type_from_price(price, order_type),
                      insert_time=self.current_time, slippage_ticks=slippage_ticks, ttl_seconds=ttl_seconds)
        self.broker.insert_order(order, reference_price=ref_price)

    def short(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None,
              slippage_ticks: float = None, order_type=None, ttl_seconds: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price is not None else price
        order = Order(symbol=symbol, direction=Direction.SHORT, offset=Offset.OPEN,
                      volume=volume, price=price, order_type=self._order_type_from_price(price, order_type),
                      insert_time=self.current_time, slippage_ticks=slippage_ticks, ttl_seconds=ttl_seconds)
        self.broker.insert_order(order, reference_price=ref_price)

    def cover(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None,
              slippage_ticks: float = None, order_type=None, ttl_seconds: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price is not None else price
        order = Order(symbol=symbol, direction=Direction.LONG, offset=Offset.CLOSE,
                      volume=volume, price=price, order_type=self._order_type_from_price(price, order_type),
                      insert_time=self.current_time, slippage_ticks=slippage_ticks, ttl_seconds=ttl_seconds)
        self.broker.insert_order(order, reference_price=ref_price)
