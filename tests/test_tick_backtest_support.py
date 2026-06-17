# -*- coding: utf-8 -*-
from datetime import datetime
import unittest

import pandas as pd

from broker.match_engine import MatchEngine
from broker.order import Direction, Offset, Order, OrderStatus, OrderType
from data_feed.aligner import DataAligner
from portfolio.account import Account


class TickBacktestSupportTest(unittest.TestCase):
    def test_tick_aligner_preserves_same_second_updates_and_volume_delta(self):
        raw = pd.DataFrame([
            {
                "symbol": "KQ.m@SHFE.rb",
                "datetime": "2025-02-25 09:00:01",
                "last_price": 3312.0,
                "volume": 480204,
                "bid_price_1": 3312.0,
                "bid_volume_1": 511,
                "ask_price_1": 3313.0,
                "ask_volume_1": 1768,
            },
            {
                "symbol": "KQ.m@SHFE.rb",
                "datetime": "2025-02-25 09:00:01",
                "last_price": 3311.0,
                "volume": 481118,
                "bid_price_1": 3311.0,
                "bid_volume_1": 1806,
                "ask_price_1": 3312.0,
                "ask_volume_1": 571,
            },
        ])

        aligned = DataAligner.align_multi_symbol(raw)

        self.assertEqual(len(aligned), 2)
        self.assertEqual(aligned.index[0], pd.Timestamp("2025-02-25 09:00:01"))
        self.assertEqual(aligned.index[1], pd.Timestamp("2025-02-25 09:00:01.000001"))
        self.assertEqual(aligned[("volume_delta", "KQ.m@SHFE.rb")].tolist(), [0.0, 914.0])
        self.assertEqual(aligned[("is_fresh", "KQ.m@SHFE.rb")].tolist(), [1.0, 1.0])

    def _broker_with_order(self, order: Order) -> MatchEngine:
        account = Account(initial_capital=1_000_000.0)
        broker = MatchEngine(account)
        accepted = broker.insert_order(order, reference_price=100.0)
        self.assertIsNone(accepted)
        self.assertEqual(len(broker.pending_orders), 1)
        return broker

    def test_tick_market_order_uses_ask_or_bid(self):
        order = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=0.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.MARKET,
            slippage_ticks=0.0,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 1), {
            "rb": {
                "open": 100.0,
                "close": 100.0,
                "bid_price_1": 100.0,
                "ask_price_1": 101.0,
            }
        })

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].price, 101.0)

    def test_tick_order_does_not_fill_on_stale_aligned_quote(self):
        order = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=0.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.MARKET,
            slippage_ticks=0.0,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 1), {
            "rb": {
                "open": 100.0,
                "close": 100.0,
                "bid_price_1": 100.0,
                "ask_price_1": 101.0,
                "is_fresh": False,
            }
        })

        self.assertEqual(trades, [])
        self.assertEqual(len(broker.pending_orders), 1)

    def test_kline_order_does_not_fill_on_stale_aligned_bar(self):
        order = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=0.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.MARKET,
            slippage_ticks=0.0,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 1), {
            "rb": {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "is_fresh": False,
            }
        })

        self.assertEqual(trades, [])
        self.assertEqual(len(broker.pending_orders), 1)

    def test_tick_opponent_order_eats_best_quote_without_extra_slippage(self):
        order = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=0.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.OPPONENT,
            slippage_ticks=5.0,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 1), {
            "rb": {
                "open": 100.0,
                "close": 100.0,
                "bid_price_1": 100.0,
                "ask_price_1": 101.0,
            }
        })

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].price, 101.0)
        self.assertEqual(trades[0].slippage_cost, 0.0)

    def test_tick_opponent_order_waits_without_valid_opponent_quote(self):
        order = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=0.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.OPPONENT,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 1), {
            "rb": {
                "open": 100.0,
                "close": 100.0,
                "bid_price_1": 100.0,
                "ask_price_1": 0.0,
            }
        })

        self.assertEqual(trades, [])
        self.assertEqual(len(broker.pending_orders), 1)

    def test_tick_limit_order_uses_current_quote(self):
        order = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=101.5,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.LIMIT,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 1), {
            "rb": {
                "open": 100.0,
                "close": 100.0,
                "bid_price_1": 100.0,
                "ask_price_1": 101.0,
            }
        })

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].price, 101.0)

    def test_pending_limit_order_expires_after_ttl(self):
        order = Order(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=99.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.LIMIT,
            ttl_seconds=1.0,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 2), {
            "rb": {
                "open": 100.0,
                "close": 100.0,
                "bid_price_1": 100.0,
                "ask_price_1": 101.0,
            }
        })

        self.assertEqual(trades, [])
        self.assertEqual(order.status, OrderStatus.CANCELED)
        self.assertEqual(len(broker.pending_orders), 0)

    def test_kline_short_limit_order_keeps_open_price_improvement(self):
        order = Order(
            symbol="rb",
            direction=Direction.SHORT,
            offset=Offset.OPEN,
            volume=1,
            price=100.0,
            insert_time=datetime(2025, 2, 25, 9, 0, 0),
            order_type=OrderType.LIMIT,
        )
        broker = self._broker_with_order(order)

        trades = broker.process_cross_section(datetime(2025, 2, 25, 9, 0, 1), {
            "rb": {
                "open": 101.0,
                "high": 105.0,
                "low": 99.0,
                "close": 101.0,
            }
        })

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].price, 101.0)


if __name__ == "__main__":
    unittest.main()
