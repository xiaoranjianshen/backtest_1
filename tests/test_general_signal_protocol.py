# -*- coding: utf-8 -*-
import unittest
from datetime import datetime

from broker.order import Direction, Offset, Order, OrderStatus, OrderType
from strategy.base import BaseStrategy
from strategy.common.execution import ExecutionPolicy
from strategy.common.rebalancer import SignalRebalancer
from strategy.common.types import normalize_signal


class _FeeModel:
    default_slippage_ticks = 0.0

    def _get_meta_data(self, symbol):
        return {"multiplier": 10, "margin_rate": 0.1, "tick_size": 1}


class _Account:
    initial_capital = 100_000
    available = 100_000
    frozen_margin = 0
    fee_model = _FeeModel()

    def get_total_equity(self, current_prices):
        return 100_000


class _Broker:
    def __init__(self, pending_orders=None):
        self.pending_orders = list(pending_orders or [])

    def cancel_order(self, order_id):
        for order in list(self.pending_orders):
            if order.order_id == order_id:
                order.status = OrderStatus.CANCELED
                self.pending_orders.remove(order)
                return True
        return False


class _Strategy:
    account = _Account()

    def __init__(self, net_position, broker=None):
        self.net_position = net_position
        self.broker = broker or _Broker()
        self.calls = []

    def get_net_position(self, symbol):
        return self.net_position

    def get_position_volume(self, symbol, direction):
        if direction == Direction.LONG:
            return max(self.net_position, 0)
        return max(-self.net_position, 0)

    def buy(self, *args, **kwargs):
        self.calls.append(("buy", args, kwargs))

    def sell(self, *args, **kwargs):
        self.calls.append(("sell", args, kwargs))

    def short(self, *args, **kwargs):
        self.calls.append(("short", args, kwargs))

    def cover(self, *args, **kwargs):
        self.calls.append(("cover", args, kwargs))


class GeneralSignalProtocolTest(unittest.TestCase):
    def _target_net(self, current_net, raw_signal):
        rebalancer = SignalRebalancer(
            _Strategy(current_net),
            sizing={"mode": "fixed_volume", "value": 2},
            execution={"order_type": "market"},
            exit_config={"close_pct": 1.0, "allow_reverse": True},
        )
        bar = {"close": 100, "open": 100, "high": 101, "low": 99}
        return rebalancer._resolve_target_net(
            "rb",
            normalize_signal(raw_signal),
            bar,
            {"rb": 100},
            current_net,
        )

    def test_partial_close_by_volume_and_percent(self):
        self.assertEqual(self._target_net(2, {"signal": 0, "close_volume": 1}), 1)
        self.assertEqual(self._target_net(2, {"signal": 0, "close_pct": 0.5}), 1)
        self.assertEqual(self._target_net(1, {"signal": 0, "close_pct": 0.5}), 0)

    def test_delta_add_and_target_percent(self):
        self.assertEqual(
            self._target_net(2, {"signal": 1, "position_mode": "delta", "size_scale": 1.0}),
            4,
        )
        self.assertEqual(self._target_net(2, {"signal": 1, "target_pct": 0.5}), 1)

    def test_unknown_position_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self._target_net(0, {"signal": 1, "position_mode": "unexpected"})

    def test_opponent_order_type_is_forwarded_to_strategy_order_api(self):
        strategy = _Strategy(0)
        rebalancer = SignalRebalancer(
            strategy,
            sizing={"mode": "fixed_volume", "value": 2},
            execution={"order_type": "opponent"},
            exit_config={"close_pct": 1.0, "allow_reverse": True},
        )

        rebalancer.rebalance({"rb": {"signal": 1}}, {"rb": {"close": 100}})

        self.assertEqual(len(strategy.calls), 1)
        self.assertEqual(strategy.calls[0][0], "buy")
        self.assertEqual(strategy.calls[0][2]["order_type"], OrderType.OPPONENT)
        self.assertIsNone(strategy.calls[0][2]["slippage_ticks"])

    def test_base_strategy_accepts_string_order_type(self):
        self.assertEqual(BaseStrategy._order_type_from_price(0.0, "opponent"), OrderType.OPPONENT)

    def test_execution_reference_price_falls_back_when_selected_field_is_invalid(self):
        execution = ExecutionPolicy({
            "order_type": "limit",
            "price_field": "mid_price",
            "limit_mode": "at_close",
        })

        price, reference_price = execution.get_order_prices(
            "rb",
            Direction.LONG,
            {"mid_price": None, "last_price": 101.0, "close": 100.0},
            _Account(),
        )

        self.assertEqual(price, 101.0)
        self.assertEqual(reference_price, 101.0)

    def test_rebalancer_cancels_conflicting_pending_order_before_new_target(self):
        pending_buy = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=99.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.LIMIT,
        )
        broker = _Broker([pending_buy])
        strategy = _Strategy(0, broker=broker)
        rebalancer = SignalRebalancer(
            strategy,
            sizing={"mode": "fixed_volume", "value": 1},
            execution={"order_type": "market"},
            exit_config={"close_pct": 1.0, "allow_reverse": True, "respect_pending_orders": True},
        )

        records = rebalancer.rebalance({"rb": {"signal": -1}}, {"rb": {"close": 100}})

        self.assertEqual(pending_buy.status, OrderStatus.CANCELED)
        self.assertEqual(len(broker.pending_orders), 0)
        self.assertEqual(strategy.calls, [("short", ("rb",), {
            "volume": 1,
            "price": 0.0,
            "reference_price": 100,
            "slippage_ticks": 1.0,
            "order_type": None,
            "ttl_seconds": None,
        })])
        self.assertEqual(records[0]["canceled_pending_orders"], 1)
        self.assertEqual(records[0]["diff"], -1)


if __name__ == "__main__":
    unittest.main()
