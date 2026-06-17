# -*- coding: utf-8 -*-
from datetime import datetime
import unittest

from broker.match_engine import MatchEngine
from broker.order import Direction, Offset, Order, OrderStatus, OrderType, Trade
from portfolio.account import Account


class AccountExecutionAccountingTest(unittest.TestCase):
    def test_close_today_uses_today_average_price_not_mixed_average(self):
        account = Account(initial_capital=1_000_000.0)
        t0 = datetime(2025, 1, 2, 9, 0, 0)

        account.process_trade(Trade(
            symbol="rb", direction=Direction.LONG, offset=Offset.OPEN,
            volume=1, price=100.0, trade_time=t0,
            commission=0.0, slippage_cost=0.0, order_id="open_yd",
        ))
        account.settle_daily({"rb": 100.0})

        account.process_trade(Trade(
            symbol="rb", direction=Direction.LONG, offset=Offset.OPEN,
            volume=1, price=120.0, trade_time=t0,
            commission=0.0, slippage_cost=0.0, order_id="open_td",
        ))
        account.process_trade(Trade(
            symbol="rb", direction=Direction.SHORT, offset=Offset.CLOSE_TODAY,
            volume=1, price=120.0, trade_time=t0,
            commission=0.0, slippage_cost=0.0, order_id="close_td",
        ))

        self.assertEqual(account.total_pnl, 0.0)
        pos = account.positions["rb_LONG"]
        self.assertEqual(pos["yd_volume"], 1)
        self.assertEqual(pos["td_volume"], 0)
        self.assertEqual(pos["yd_avg_price"], 100.0)
        self.assertEqual(pos["avg_price"], 100.0)

    def test_pending_close_order_is_revalidated_at_fill_time(self):
        account = Account(initial_capital=1_000_000.0)
        broker = MatchEngine(account)
        t0 = datetime(2025, 1, 2, 9, 0, 0)

        account.process_trade(Trade(
            symbol="rb", direction=Direction.LONG, offset=Offset.OPEN,
            volume=1, price=100.0, trade_time=t0,
            commission=0.0, slippage_cost=0.0, order_id="open",
        ))
        account.settle_daily({"rb": 100.0})

        close_order = Order(
            symbol="rb", direction=Direction.SHORT, offset=Offset.CLOSE,
            volume=1, price=0.0, insert_time=t0,
            order_type=OrderType.MARKET, slippage_ticks=0.0,
        )
        broker.insert_order(close_order, reference_price=100.0)
        self.assertEqual(len(broker.pending_orders), 1)

        account.positions["rb_LONG"]["yd_volume"] = 0
        trades = broker.process_cross_section(t0, {
            "rb": {
                "open": 101.0,
                "high": 101.0,
                "low": 101.0,
                "close": 101.0,
                "is_fresh": True,
            }
        })

        self.assertEqual(trades, [])
        self.assertEqual(close_order.status, OrderStatus.REJECTED)
        self.assertEqual(len(broker.pending_orders), 0)


if __name__ == "__main__":
    unittest.main()
