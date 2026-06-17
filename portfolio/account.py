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

    def estimate_required_cash(self, order: Order, reference_price: float) -> float:
        if order.offset != Offset.OPEN:
            return 0.0
        raw_code = pure_product_code(order.symbol)
        margin = self.estimate_required_margin(raw_code, order.volume, reference_price)
        commission = self.fee_model.calculate_commission(raw_code, reference_price, order.volume, Offset.OPEN)
        return margin + commission

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
            self.positions[pos_key] = {
                'yd_volume': 0,
                'td_volume': 0,
                'yd_avg_price': 0.0,
                'td_avg_price': 0.0,
                'avg_price': 0.0,
                'frozen_margin': 0.0,
            }

    @staticmethod
    def _weighted_avg(price_a: float, vol_a: int, price_b: float, vol_b: int) -> float:
        total = vol_a + vol_b
        if total <= 0:
            return 0.0
        return (price_a * vol_a + price_b * vol_b) / total

    def _sync_position_avg(self, pos: dict):
        pos['avg_price'] = self._weighted_avg(
            pos.get('yd_avg_price', 0.0), pos.get('yd_volume', 0),
            pos.get('td_avg_price', 0.0), pos.get('td_volume', 0),
        )

    def _normalise_position(self, pos: dict):
        if not pos:
            return
        legacy_avg = float(pos.get('avg_price', 0.0) or 0.0)
        if 'yd_volume' not in pos and 'td_volume' not in pos and 'volume' in pos:
            pos['yd_volume'] = pos.get('volume', 0)
            pos['td_volume'] = 0
        pos.setdefault('yd_volume', 0)
        pos.setdefault('td_volume', 0)
        pos.setdefault('yd_avg_price', legacy_avg if pos.get('yd_volume', 0) > 0 else 0.0)
        pos.setdefault('td_avg_price', legacy_avg if pos.get('td_volume', 0) > 0 else 0.0)
        pos.setdefault('frozen_margin', 0.0)
        self._sync_position_avg(pos)

    def settle_daily(self, settlement_prices: dict = None):
        """
        日切结算：将当日新开仓并入昨仓，供下一交易日平今/平昨路由使用。
        """
        settlement_prices = settlement_prices or {}
        for pos_key, pos in list(self.positions.items()):
            self._normalise_position(pos)
            volume = self._position_volume(pos)
            if volume <= 0:
                del self.positions[pos_key]
                continue

            symbol, direction = pos_key.rsplit('_', 1)
            current_avg = pos['avg_price']
            settlement_price = settlement_prices.get(symbol)

            if settlement_price is not None:
                settlement_price = float(settlement_price)
                meta = self.fee_model._get_meta_data(symbol)
                multiplier = meta['multiplier']
                margin_rate = meta['margin_rate']

                if direction == Direction.LONG.value:
                    settlement_pnl = (settlement_price - current_avg) * volume * multiplier
                else:
                    settlement_pnl = (current_avg - settlement_price) * volume * multiplier

                old_margin = pos.get('frozen_margin', 0.0)
                new_margin = settlement_price * volume * multiplier * margin_rate
                self.total_pnl += settlement_pnl
                self.available += settlement_pnl
                self.available += old_margin - new_margin
                self.frozen_margin = max(0.0, self.frozen_margin - old_margin + new_margin)
                pos['frozen_margin'] = new_margin
                pos['yd_avg_price'] = settlement_price
            else:
                pos['yd_avg_price'] = current_avg

            pos['yd_volume'] = volume
            pos['td_volume'] = 0
            pos['td_avg_price'] = 0.0
            self._sync_position_avg(pos)

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
            self._normalise_position(pos_info)
            open_price = pos_info['avg_price']
            multiplier = self.fee_model._get_meta_data(symbol)['multiplier']

            if direction == 'LONG':
                unrealized_pnl += (current_price - open_price) * volume * multiplier
            else:
                unrealized_pnl += (open_price - current_price) * volume * multiplier

        return self.available + self.frozen_margin + unrealized_pnl

    def check_order_validation(
            self,
            order: Order,
            reference_price: float,
            pending_cash_adjustment: float = 0.0,
            available_adjustment: float = 0.0,
    ) -> bool:
        raw_code = pure_product_code(order.symbol)

        if order.offset == Offset.OPEN:
            # Opening orders reserve margin plus estimated commission.
            required_cash = self.estimate_required_cash(order, reference_price)
            effective_pending = max(0.0, self.pending_margin - pending_cash_adjustment)
            effective_available = self.available + available_adjustment - effective_pending
            if effective_available < required_cash:
                print(
                    f"[Risk Check] 资金不足：拟开仓 {raw_code} {order.volume}手，"
                    f"需保证金+预估手续费:￥{required_cash:.2f}，"
                    f"可用(扣挂单):￥{effective_available:.2f}"
                )
                return False
            return True

        target_pos_dir = Direction.LONG if order.direction == Direction.SHORT else Direction.SHORT
        pos_key = self._get_position_key(raw_code, target_pos_dir)
        pos = self.positions.get(pos_key, {})
        self._normalise_position(pos)

        if order.offset == Offset.CLOSE:
            yd_vol = pos.get('yd_volume', 0)
            if yd_vol < order.volume:
                print(
                    f"[Risk Check] 昨仓不足：拟平昨 {raw_code} {order.volume}手，"
                    f"实际昨仓仅有: {yd_vol}手"
                )
                return False
            return True

        if order.offset == Offset.CLOSE_TODAY:
            td_vol = pos.get('td_volume', 0)
            if td_vol < order.volume:
                print(
                    f"[Risk Check] 今仓不足：拟平今 {raw_code} {order.volume}手，"
                    f"实际今仓仅有: {td_vol}手"
                )
                return False
            return True

        return True

    def reserve_pending_margin(self, order: Order, reference_price: float):
        if order.offset == Offset.OPEN:
            self.pending_margin += self.estimate_required_cash(order, reference_price)

    def release_pending_margin(self, order: Order, reference_price: float):
        if order.offset == Offset.OPEN:
            reserved = self.estimate_required_cash(order, reference_price)
            self.pending_margin = max(0.0, self.pending_margin - reserved)

    def process_trade(self, trade: Trade):
        raw_code = pure_product_code(trade.symbol)
        meta = self.fee_model._get_meta_data(raw_code)
        multiplier = meta['multiplier']
        margin_rate = meta['margin_rate']

        if trade.offset == Offset.OPEN:
            pos_key = self._get_position_key(raw_code, trade.direction)
            self._init_position(pos_key)

            pos = self.positions[pos_key]
            self._normalise_position(pos)
            old_td_vol = pos.get('td_volume', 0)
            new_td_vol = old_td_vol + trade.volume
            pos['td_avg_price'] = self._weighted_avg(
                pos.get('td_avg_price', 0.0), old_td_vol,
                trade.price, trade.volume,
            )
            pos['td_volume'] += trade.volume
            self._sync_position_avg(pos)

            margin_locked = trade.price * trade.volume * multiplier * margin_rate
            self.available -= trade.commission
            self.available -= margin_locked
            self.frozen_margin += margin_locked
            pos['frozen_margin'] = pos.get('frozen_margin', 0.0) + margin_locked
            return

        target_pos_dir = Direction.LONG if trade.direction == Direction.SHORT else Direction.SHORT
        pos_key = self._get_position_key(raw_code, target_pos_dir)
        pos = self.positions.get(pos_key)
        if not pos:
            return

        self._normalise_position(pos)
        pos_total_before = self._position_volume(pos)
        if pos_total_before <= 0:
            return

        if trade.offset == Offset.CLOSE_TODAY:
            if pos.get('td_volume', 0) < trade.volume:
                return
            open_avg_price = pos.get('td_avg_price', pos['avg_price'])
        else:
            if pos.get('yd_volume', 0) < trade.volume:
                return
            open_avg_price = pos.get('yd_avg_price', pos['avg_price'])

        self.available -= trade.commission

        if target_pos_dir == Direction.LONG:
            pnl = (trade.price - open_avg_price) * trade.volume * multiplier
        else:
            pnl = (open_avg_price - trade.price) * trade.volume * multiplier

        self.total_pnl += pnl
        self.available += pnl

        if trade.offset == Offset.CLOSE_TODAY:
            pos['td_volume'] -= trade.volume
            if pos['td_volume'] <= 0:
                pos['td_avg_price'] = 0.0
        else:
            pos['yd_volume'] -= trade.volume
            if pos['yd_volume'] <= 0:
                pos['yd_avg_price'] = 0.0

        pos_frozen_margin = pos.get(
            'frozen_margin',
            open_avg_price * pos_total_before * multiplier * margin_rate
        )
        margin_released = pos_frozen_margin * (trade.volume / pos_total_before)
        self.available += margin_released
        self.frozen_margin = max(0.0, self.frozen_margin - margin_released)
        pos['frozen_margin'] = max(0.0, pos_frozen_margin - margin_released)
        self._sync_position_avg(pos)

        if self._position_volume(pos) <= 0:
            del self.positions[pos_key]

    def print_status(self, current_time, current_prices: dict = None):
        equity = self.get_total_equity(current_prices) if current_prices else self.available + self.frozen_margin
        print(
            f"\n[Account] {current_time} | 动态权益: ￥{equity:,.2f} | "
            f"可用: ￥{self.available:,.2f} | 占用: ￥{self.frozen_margin:,.2f} | "
            f"累计平仓盈亏: ￥{self.total_pnl:,.2f}"
        )
