# -*- coding: utf-8 -*-
"""
策略基类 (Base Strategy & Portfolio Template)
作用：支持单/多品种、全时间周期，提供底层 API 与自动化截面调仓引擎。
"""
from datetime import datetime
from typing import Dict, List, Union
import pandas as pd

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

    # 💥 这里是被 Cursor 毁掉的核心：恢复正确的物理仓位查询
    def get_position_volume(self, symbol: str, direction: Direction) -> int:
        from config import pure_product_code
        raw_code = pure_product_code(symbol)
        pos_key = self.account._get_position_key(raw_code, direction)
        pos = self.account.positions.get(pos_key)
        return self.account._position_volume(pos) if pos else 0

    def get_net_position(self, symbol: str) -> int:
        long_vol = self.get_position_volume(symbol, Direction.LONG)
        short_vol = self.get_position_volume(symbol, Direction.SHORT)
        return long_vol - short_vol

    def buy(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price else price
        order = Order(symbol=symbol, direction=Direction.LONG, offset=Offset.OPEN,
                      volume=volume, price=price, order_type=OrderType.MARKET if price == 0.0 else OrderType.LIMIT,
                      insert_time=self.current_time)
        self.broker.insert_order(order, reference_price=ref_price)

    def sell(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price else price
        order = Order(symbol=symbol, direction=Direction.SHORT, offset=Offset.CLOSE,
                      volume=volume, price=price, order_type=OrderType.MARKET if price == 0.0 else OrderType.LIMIT,
                      insert_time=self.current_time)
        self.broker.insert_order(order, reference_price=ref_price)

    def short(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price else price
        order = Order(symbol=symbol, direction=Direction.SHORT, offset=Offset.OPEN,
                      volume=volume, price=price, order_type=OrderType.MARKET if price == 0.0 else OrderType.LIMIT,
                      insert_time=self.current_time)
        self.broker.insert_order(order, reference_price=ref_price)

    def cover(self, symbol: str, volume: int, price: float = 0.0, reference_price: float = None):
        if volume <= 0: return
        ref_price = reference_price if reference_price else price
        order = Order(symbol=symbol, direction=Direction.LONG, offset=Offset.CLOSE,
                      volume=volume, price=price, order_type=OrderType.MARKET if price == 0.0 else OrderType.LIMIT,
                      insert_time=self.current_time)
        self.broker.insert_order(order, reference_price=ref_price)


class PortfolioTemplate(BaseStrategy):
    def on_bar(self, current_time: datetime, bar_data: dict):
        self.current_time = current_time
        target_portfolio = self.generate_target_portfolio(bar_data)
        if not target_portfolio: return
        self._rebalance(target_portfolio, bar_data)

    def generate_target_portfolio(self, bar_data: dict) -> Dict[str, int]:
        raise NotImplementedError("多品种策略必须实现 generate_target_portfolio 方法！")

    def _rebalance(self, target_portfolio: Dict[str, int], bar_data: dict):
        for sym, target_net in target_portfolio.items():
            if sym not in bar_data or pd.isna(bar_data[sym].get('close', pd.NA)):
                continue
            current_close = bar_data[sym]['close']
            long_vol = self.get_position_volume(sym, Direction.LONG)
            short_vol = self.get_position_volume(sym, Direction.SHORT)
            current_net = long_vol - short_vol
            diff = target_net - current_net
            if diff == 0: continue

            if diff > 0:
                cover_vol = min(diff, short_vol)
                if cover_vol > 0:
                    self.cover(sym, volume=cover_vol, price=0.0, reference_price=current_close)
                    diff -= cover_vol
                if diff > 0:
                    self.buy(sym, volume=diff, price=0.0, reference_price=current_close)
            elif diff < 0:
                sell_target = abs(diff)
                sell_vol = min(sell_target, long_vol)
                if sell_vol > 0:
                    self.sell(sym, volume=sell_vol, price=0.0, reference_price=current_close)
                    sell_target -= sell_vol
                if sell_target > 0:
                    self.short(sym, volume=sell_target, price=0.0, reference_price=current_close)