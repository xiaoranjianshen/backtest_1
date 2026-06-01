# -*- coding: utf-8 -*-
"""
资产与账户管理 (Portfolio & Account)
职责：记录可用资金、计算保证金占用、管理多空持仓、今昨仓拆分、日切结算、动态盯市
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from broker.order import Order, Trade, Direction, Offset
from broker.fee_model import FeeModel
from config import pure_product_code


class Account:
    """实盘级虚拟账户"""

    def __init__(self, initial_capital: float = 1000000.0):
        self.initial_capital = initial_capital
        self.available = initial_capital
        self.frozen_margin = 0.0
        self.total_pnl = 0.0
        self.positions = {}
        self.fee_model = FeeModel()
        self.pending_margin = 0.0

    def estimate_required_margin(self, symbol: str, volume: int, reference_price: float) -> float:
        raw_code = pure_product_code(symbol)
        meta = self.fee_model._get_meta_data(raw_code)
        return reference_price * volume * meta['multiplier'] * meta['margin_rate']

    def _get_position_key(self, symbol: str, direction: Direction) -> str:
        return f"{symbol}_{direction.value}"

    @staticmethod
    def _position_volume(pos_info: dict) -> int:
        if not pos_info:
            return 0
        if 'yd_volume' in pos_info or 'td_volume' in pos_info:
            return pos_info.get('yd_volume', 0) + pos_info.get('td_volume', 0)
        return pos_info.get('volume', 0)

    def _init_position(self, pos_key: str):
        if pos_key not in self.positions:
            self.positions[pos_key] = {'yd_volume': 0, 'td_volume': 0, 'avg_price': 0.0}

    def settle_daily(self):
        """
        日切结算：将当日新开仓并入昨仓，供下一交易日平今/平昨路由使用。
        """
        for pos in self.positions.values():
            pos['yd_volume'] = self._position_volume(pos)
            pos['td_volume'] = 0

    def get_total_equity(self, current_prices: dict) -> float:
        """盯市总权益 = 可用 + 冻结保证金 + 浮动盈亏"""
        unrealized_pnl = 0.0
        for pos_key, pos_info in self.positions.items():
            symbol, direction = pos_key.rsplit('_', 1)
            if symbol not in current_prices:
                continue

            volume = self._position_volume(pos_info)
            if volume <= 0:
                continue

            current_price = current_prices[symbol]
            open_price = pos_info['avg_price']
            multiplier = self.fee_model._get_meta_data(symbol)['multiplier']

            if direction == 'LONG':
                unrealized_pnl += (current_price - open_price) * volume * multiplier
            else:
                unrealized_pnl += (open_price - current_price) * volume * multiplier

        return self.available + self.frozen_margin + unrealized_pnl

    def check_order_validation(self, order: Order, reference_price: float) -> bool:
        raw_code = pure_product_code(order.symbol)
        meta = self.fee_model._get_meta_data(raw_code)
        margin_rate = meta['margin_rate']
        multiplier = meta['multiplier']

        if order.offset == Offset.OPEN:
            required_margin = self.estimate_required_margin(raw_code, order.volume, reference_price)
            effective_available = self.available - self.pending_margin
            if effective_available < required_margin:
                print(
                    f"❌ [风控拦截] 资金不足！拟开仓 {raw_code} {order.volume}手，"
                    f"需保证金:￥{required_margin:.2f}，可用(扣挂单):￥{effective_available:.2f}"
                )
                return False
            return True

        target_pos_dir = Direction.LONG if order.direction == Direction.SHORT else Direction.SHORT
        pos_key = self._get_position_key(raw_code, target_pos_dir)

        if order.offset == Offset.CLOSE:
            yd_vol = self.positions.get(pos_key, {}).get('yd_volume', 0)
            if yd_vol < order.volume:
                print(
                    f"❌ [风控拦截] 昨仓不足！拟平昨 {raw_code} {order.volume}手，"
                    f"实际昨仓仅有: {yd_vol}手"
                )
                return False
            return True

        if order.offset == Offset.CLOSE_TODAY:
            td_vol = self.positions.get(pos_key, {}).get('td_volume', 0)
            if td_vol < order.volume:
                print(
                    f"❌ [风控拦截] 今仓不足！拟平今 {raw_code} {order.volume}手，"
                    f"实际今仓仅有: {td_vol}手"
                )
                return False
            return True

        return True

    def reserve_pending_margin(self, order: Order, reference_price: float):
        if order.offset == Offset.OPEN:
            self.pending_margin += self.estimate_required_margin(order.symbol, order.volume, reference_price)

    def release_pending_margin(self, order: Order, reference_price: float):
        if order.offset == Offset.OPEN:
            reserved = self.estimate_required_margin(order.symbol, order.volume, reference_price)
            self.pending_margin = max(0.0, self.pending_margin - reserved)

    def process_trade(self, trade: Trade):
        raw_code = pure_product_code(trade.symbol)
        meta = self.fee_model._get_meta_data(raw_code)
        multiplier = meta['multiplier']
        margin_rate = meta['margin_rate']

        self.available -= trade.commission

        if trade.offset == Offset.OPEN:
            pos_key = self._get_position_key(raw_code, trade.direction)
            self._init_position(pos_key)

            pos = self.positions[pos_key]
            old_vol = self._position_volume(pos)
            new_vol = old_vol + trade.volume

            pos['avg_price'] = (
                (pos['avg_price'] * old_vol + trade.price * trade.volume) / new_vol
                if new_vol > 0 else trade.price
            )
            pos['td_volume'] += trade.volume

            margin_locked = trade.price * trade.volume * multiplier * margin_rate
            self.available -= margin_locked
            self.frozen_margin += margin_locked
            return

        target_pos_dir = Direction.LONG if trade.direction == Direction.SHORT else Direction.SHORT
        pos_key = self._get_position_key(raw_code, target_pos_dir)
        pos = self.positions.get(pos_key)
        if not pos:
            return
        open_avg_price = pos['avg_price']

        if target_pos_dir == Direction.LONG:
            pnl = (trade.price - open_avg_price) * trade.volume * multiplier
        else:
            pnl = (open_avg_price - trade.price) * trade.volume * multiplier

        self.total_pnl += pnl
        self.available += pnl

        if trade.offset == Offset.CLOSE_TODAY:
            pos['td_volume'] -= trade.volume
        else:
            pos['yd_volume'] -= trade.volume

        margin_released = open_avg_price * trade.volume * multiplier * margin_rate
        self.available += margin_released
        self.frozen_margin -= margin_released

        if self._position_volume(pos) <= 0:
            del self.positions[pos_key]

    def print_status(self, current_time, current_prices: dict = None):
        equity = self.get_total_equity(current_prices) if current_prices else self.available + self.frozen_margin
        print(
            f"\n[{current_time}] 💰 账户快照 | 动态权益: ￥{equity:,.2f} | "
            f"可用: ￥{self.available:,.2f} | 占用: ￥{self.frozen_margin:,.2f} | "
            f"累计平仓盈亏: ￥{self.total_pnl:,.2f}"
        )
