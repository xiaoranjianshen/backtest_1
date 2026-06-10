# -*- coding: utf-8 -*-
import unittest

from broker.order import Direction
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
    pending_orders = []


class _Strategy:
    account = _Account()
    broker = _Broker()

    def __init__(self, net_position):
        self.net_position = net_position

    def get_net_position(self, symbol):
        return self.net_position

    def get_position_volume(self, symbol, direction):
        if direction == Direction.LONG:
            return max(self.net_position, 0)
        return max(-self.net_position, 0)

    def buy(self, *args, **kwargs):
        pass

    def sell(self, *args, **kwargs):
        pass

    def short(self, *args, **kwargs):
        pass

    def cover(self, *args, **kwargs):
        pass


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


if __name__ == "__main__":
    unittest.main()
