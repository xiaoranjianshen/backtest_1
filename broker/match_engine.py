# -*- coding: utf-8 -*-
"""
模拟撮合引擎 - 核心撮合循环器 (Match Engine)
机制：支持 Market/Limit 撮合、资金与持仓严格风控、支持手动撤单
"""
from typing import List, Dict
from datetime import datetime
import dataclasses
import uuid
import pandas as pd

from .order import Order, Trade, OrderStatus, Direction, Offset, OrderType
from .fee_model import FeeModel
from portfolio.account import Account
from config import pure_product_code, trade_symbol_code


class MatchEngine:
    def __init__(self, account: Account):
        self.account = account
        self.fee_model = FeeModel(default_slippage_ticks=1)
        self.pending_orders: List[Order] = []
        self.trade_history: List[Trade] = []

    def _enqueue_order(self, order: Order, reference_price: float) -> bool:
        """过风控后入队，并冻结预估保证金"""
        available_adjustment = self._estimate_pending_close_margin_release(order)
        if not self.account.check_order_validation(
                order,
                reference_price,
                available_adjustment=available_adjustment,
        ):
            order.status = OrderStatus.REJECTED
            return False
        order.reference_price = reference_price
        self.account.reserve_pending_margin(order, reference_price)
        self.pending_orders.append(order)
        return True

    def _estimate_pending_close_margin_release(self, order: Order) -> float:
        if order.offset != Offset.OPEN:
            return 0.0

        raw_code = trade_symbol_code(order.symbol)
        target_pos_dir = Direction.LONG if order.direction == Direction.SHORT else Direction.SHORT
        pos_key = self.account._get_position_key(raw_code, target_pos_dir)
        pos = self.account.positions.get(pos_key)
        if not pos:
            return 0.0

        total_volume = self.account._position_volume(pos)
        if total_volume <= 0:
            return 0.0

        pending_close_volume = 0
        for pending in self.pending_orders:
            if pending.offset not in (Offset.CLOSE, Offset.CLOSE_TODAY):
                continue
            if trade_symbol_code(pending.symbol) != raw_code:
                continue
            if pending.direction != order.direction:
                continue
            pending_close_volume += pending.volume

        if pending_close_volume <= 0:
            return 0.0

        releasable_volume = min(pending_close_volume, total_volume)
        return pos.get('frozen_margin', 0.0) * (releasable_volume / total_volume)

    def _reserved_cash_for_order(self, order: Order) -> float:
        if order.offset != Offset.OPEN:
            return 0.0
        reference_price = getattr(order, 'reference_price', None)
        if reference_price is None:
            return 0.0
        return self.account.estimate_required_cash(order, reference_price)

    def _validate_order_at_fill(self, order: Order, exec_price: float) -> bool:
        self._reroute_close_offset_at_fill(order)
        reserved_cash = self._reserved_cash_for_order(order)
        return self.account.check_order_validation(
            order,
            exec_price,
            pending_cash_adjustment=reserved_cash,
        )

    def _reroute_close_offset_at_fill(self, order: Order):
        if order.offset not in (Offset.CLOSE, Offset.CLOSE_TODAY):
            return

        raw_code = trade_symbol_code(order.symbol)
        target_pos_dir = Direction.LONG if order.direction == Direction.SHORT else Direction.SHORT
        pos_key = self.account._get_position_key(raw_code, target_pos_dir)
        pos = self.account.positions.get(pos_key, {})
        self.account._normalise_position(pos)

        yd_vol = pos.get('yd_volume', 0)
        td_vol = pos.get('td_volume', 0)
        if order.offset == Offset.CLOSE_TODAY and td_vol < order.volume <= yd_vol:
            order.offset = Offset.CLOSE
        elif order.offset == Offset.CLOSE and yd_vol < order.volume <= td_vol:
            order.offset = Offset.CLOSE_TODAY

    def _reject_pending_order(self, order: Order, reason: str):
        order.status = OrderStatus.REJECTED
        reference_price = getattr(order, 'reference_price', None)
        if reference_price is not None:
            self.account.release_pending_margin(order, reference_price)
        if order in self.pending_orders:
            self.pending_orders.remove(order)
        print(f"[Broker Reject] {order.order_id} | {reason}")

    def insert_order(self, order: Order, reference_price: float):
        """策略下达订单，引擎智能路由平今/平昨，再过风控"""

        raw_code = trade_symbol_code(order.symbol)
        target_pos_dir = Direction.LONG if order.direction == Direction.SHORT else Direction.SHORT
        pos_key = self.account._get_position_key(raw_code, target_pos_dir)
        pos_info = self.account.positions.get(pos_key, {})
        self.account._normalise_position(pos_info)
        yd_vol = pos_info.get('yd_volume', 0)
        td_vol = pos_info.get('td_volume', 0)

        if order.offset == Offset.CLOSE:
            # ---- 平仓保护：如果持仓不足，截断到实际持仓 ----
            total_pos = yd_vol + td_vol

            if total_pos == 0:
                # 无任何持仓，直接拒绝
                order.status = OrderStatus.REJECTED
                return

            if total_pos < order.volume:
                # 持仓不够，按现有持仓量平仓，并给出警告
                print(
                    f"[Broker Warning] 平仓{order.volume}手，实际持仓仅{total_pos}手"
                    f"(昨:{yd_vol} 今:{td_vol})，截断为{total_pos}手"
                )
                order.volume = total_pos
                # 注意：这里 order 是可变对象，修改 volume 会影响调用方，按需深拷贝

            # ---- 智能路由：优先平昨，再平今 ----
            if yd_vol >= order.volume:
                # 昨仓足够，全部平昨
                order.offset = Offset.CLOSE
                self._enqueue_order(order, reference_price)

            elif yd_vol > 0:
                # 昨仓部分 + 今仓部分：拆成两单
                order1 = dataclasses.replace(order, volume=yd_vol, offset=Offset.CLOSE)
                order2 = dataclasses.replace(
                    order,
                    volume=order.volume - yd_vol,
                    offset=Offset.CLOSE_TODAY,
                    order_id=f"ORD_{uuid.uuid4().hex[:8]}",
                )
                # 直接入队两个子订单，原始 order 丢弃
                self._enqueue_order(order1, reference_price)
                self._enqueue_order(order2, reference_price)

            elif td_vol > 0:
                # 只有今仓，全部平今
                order.offset = Offset.CLOSE_TODAY
                self._enqueue_order(order, reference_price)

            # 注意：经过前面 total_pos==0 的提前返回，这里不可能 total_pos==0

        else:
            # 开仓或其他非平仓指令，直接送入队列
            self._enqueue_order(order, reference_price)

    def cancel_order(self, order_id: str) -> bool:
        for order in self.pending_orders:
            if order.order_id == order_id:
                order.status = OrderStatus.CANCELED
                self.account.release_pending_margin(order, order.reference_price)
                self.pending_orders.remove(order)
                print(f"[Broker Cancel] 订单 {order_id} 已成功撤销。")
                return True
        return False

    @staticmethod
    def _valid_price(value) -> bool:
        return value is not None and not pd.isna(value) and float(value) > 0

    @staticmethod
    def _is_tick_bar(bar: dict) -> bool:
        return 'bid_price_1' in bar or 'ask_price_1' in bar

    @staticmethod
    def _is_expired(order: Order, current_time: datetime) -> bool:
        if order.ttl_seconds is None:
            return False
        if order.ttl_seconds <= 0:
            return False
        if order.insert_time is None:
            return False
        return (current_time - order.insert_time).total_seconds() > float(order.ttl_seconds)

    def _cancel_expired_order(self, order: Order):
        order.status = OrderStatus.CANCELED
        self.account.release_pending_margin(order, order.reference_price)
        if order in self.pending_orders:
            self.pending_orders.remove(order)

    def _tick_market_price(self, bar: dict, direction: Direction):
        bid = bar.get('bid_price_1')
        ask = bar.get('ask_price_1')
        if direction == Direction.LONG and self._valid_price(ask):
            return float(ask)
        if direction == Direction.SHORT and self._valid_price(bid):
            return float(bid)
        return None

    def _tick_limit_price(self, bar: dict, direction: Direction, limit_price: float):
        bid = bar.get('bid_price_1')
        ask = bar.get('ask_price_1')
        if direction == Direction.LONG and self._valid_price(ask) and float(ask) <= limit_price:
            return float(ask)
        if direction == Direction.SHORT and self._valid_price(bid) and float(bid) >= limit_price:
            return float(bid)
        return None

    def process_cross_section(self, current_time: datetime, bar_data: Dict[str, dict]):
        """核心撮合循环 (高低点穿透与清算结算)"""
        new_trades = []

        if not self.pending_orders:
            return new_trades

        for order in list(self.pending_orders):
            if self._is_expired(order, current_time):
                self._cancel_expired_order(order)
                print(f"[Broker Cancel] {current_time} | order {order.order_id} expired")
                continue

            raw_code = trade_symbol_code(order.symbol)
            if raw_code not in bar_data:
                continue

            bar = bar_data[raw_code]
            is_tick_bar = self._is_tick_bar(bar)
            if not bool(bar.get('is_fresh', True)):
                continue
            if is_tick_bar:
                has_price = (
                    self._valid_price(bar.get('bid_price_1'))
                    or self._valid_price(bar.get('ask_price_1'))
                    or self._valid_price(bar.get('open'))
                )
            else:
                has_price = self._valid_price(bar.get('open'))
            if not has_price:
                continue

            is_filled = False
            exec_price = 0.0
            slippage_cost_value = 0.0

            if order.order_type in (OrderType.MARKET, OrderType.OPPONENT):
                # 市价单按下一根 bar 的 open 成交，并按订单/策略配置追加不利滑点。
                slippage = 0.0 if order.order_type == OrderType.OPPONENT else self.fee_model.calculate_slippage(
                    raw_code, order.slippage_ticks
                )
                base_price = self._tick_market_price(bar, order.direction) if is_tick_bar else None
                if order.order_type == OrderType.OPPONENT and base_price is None:
                    continue
                if base_price is None:
                    base_price = float(bar['open'])
                is_filled = True
                exec_price = base_price + slippage if order.direction == Direction.LONG else base_price - slippage
                multiplier = self.fee_model._get_meta_data(raw_code)['multiplier']
                slippage_cost_value = slippage * order.volume * multiplier

            elif order.order_type == OrderType.LIMIT:
                if is_tick_bar:
                    tick_fill_price = self._tick_limit_price(bar, order.direction, float(order.price))
                    if tick_fill_price is not None:
                        is_filled = True
                        exec_price = tick_fill_price
                elif order.direction == Direction.LONG and bar['low'] <= order.price:
                    is_filled = True
                    exec_price = min(order.price, bar['open'])
                elif order.direction == Direction.SHORT and bar['high'] >= order.price:
                    is_filled = True
                    exec_price = max(order.price, bar['open'])
            if is_filled:
                if not self._validate_order_at_fill(order, exec_price):
                    self._reject_pending_order(order, "order state changed before fill")
                    continue

                commission = self.fee_model.calculate_commission(
                    symbol=raw_code, price=exec_price, volume=order.volume, offset=order.offset
                )

                trade = Trade(
                    symbol=raw_code, direction=order.direction, offset=order.offset,
                    volume=order.volume, price=exec_price, trade_time=current_time,
                    commission=commission,
                    slippage_cost=slippage_cost_value,
                    order_id=order.order_id
                )

                order.status = OrderStatus.FILLED
                order.filled_volume = order.volume
                order.filled_price = exec_price

                self.trade_history.append(trade)
                new_trades.append(trade)
                self.pending_orders.remove(order)
                self.account.release_pending_margin(order, order.reference_price)
                self.account.process_trade(trade)

                print(
                    f"[Broker Match] {current_time} | {order.offset.value} {order.direction.value} {raw_code} "
                    f"{order.volume}手 | 成交价:{exec_price} | 费:￥{commission:.2f}"
                )

        return new_trades

    def execute_rollover(self, symbol: str, pos_direction: Direction, volume: int,
                         old_close_price: float, roll_open_price: float, current_time: datetime):
        """
        换月：按昨日收盘价平旧仓，按T日开盘价开新仓
        - old_close_price: 昨日收盘价（用于结算旧仓，捕获T-1日→T日的真实盈亏）
        - roll_open_price: T日开盘价（用于开新仓）
        """
        raw_code = pure_product_code(symbol)
        account = self.account
        pos_key = account._get_position_key(raw_code, pos_direction)
        pos = account.positions.get(pos_key)
        if not pos or account._position_volume(pos) < volume:
            return

        close_direction = Direction.SHORT if pos_direction == Direction.LONG else Direction.LONG
        yd_vol = pos.get('yd_volume', 0)

        # 先平昨仓
        close_yd = min(volume, yd_vol)
        if close_yd > 0:
            self._execute_rollover_close(
                raw_code, close_direction, pos_direction, close_yd,
                Offset.CLOSE, old_close_price, current_time
            )

        # 再平今仓
        remaining = volume - close_yd
        if remaining > 0:
            self._execute_rollover_close(
                raw_code, close_direction, pos_direction, remaining,
                Offset.CLOSE_TODAY, old_close_price, current_time
            )

        # 换月开新仓等价于系统市价换仓，使用 fee_model 的默认滑点。
        # GeneralSignalStrategy 会把策略 execution.slippage_ticks 同步到这里。
        slippage = self.fee_model.calculate_slippage(raw_code)
        exec_open = roll_open_price + slippage if pos_direction == Direction.LONG else roll_open_price - slippage
        multiplier = self.fee_model._get_meta_data(raw_code)['multiplier']
        slippage_cost = slippage * volume * multiplier
        commission = self.fee_model.calculate_commission(raw_code, exec_open, volume, Offset.OPEN)

        open_trade = Trade(
            symbol=raw_code, direction=pos_direction, offset=Offset.OPEN,
            volume=volume, price=exec_open, trade_time=current_time,
            commission=commission, slippage_cost=slippage_cost,
            order_id=f"ROL_OPEN_{raw_code}_{current_time.strftime('%Y%m%d%H%M%S')}",
            is_rollover=True,
        )
        self.trade_history.append(open_trade)
        account.process_trade(open_trade)

        print(f"   -> 开{pos_direction.value} {volume}手 @{exec_open:.1f} | 费:￥{commission:.2f}")

    def _execute_rollover_close(self, raw_code: str, close_direction: Direction,
                                pos_direction: Direction, volume: int,
                                close_offset: Offset, close_price: float, current_time: datetime):
        """换月平仓：按昨日收盘价结算，走正常流程"""
        account = self.account
        pos_key = account._get_position_key(raw_code, pos_direction)
        pos = account.positions.get(pos_key)
        if not pos:
            return

        commission = self.fee_model.calculate_commission(raw_code, close_price, volume, close_offset)

        close_trade = Trade(
            symbol=raw_code, direction=close_direction, offset=close_offset,
            volume=volume, price=close_price, trade_time=current_time,
            commission=commission, slippage_cost=0.0,
            order_id=f"ROL_CLOSE_{raw_code}_{current_time.strftime('%Y%m%d%H%M%S')}_{close_offset.value}",
            is_rollover=True,
        )
        self.trade_history.append(close_trade)
        account.process_trade(close_trade)

        print(f"   -> 平{pos_direction.value} {volume}手 @{close_price:.1f} | 费:￥{commission:.2f}")
