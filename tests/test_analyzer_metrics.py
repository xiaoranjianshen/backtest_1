# -*- coding: utf-8 -*-
import unittest
from datetime import datetime

import pandas as pd

from analyzer.performance import StrategyAnalyzer
from broker.order import Direction, Offset, Trade


class AnalyzerMetricsTest(unittest.TestCase):
    def test_trade_days_and_market_days_are_reported_separately(self):
        equity_df = pd.DataFrame([
            {"datetime": "2026-04-01 09:00:00", "equity": 1_000_000.0, "position_notional": 0.0},
            {"datetime": "2026-04-01 15:00:00", "equity": 1_000_100.0, "position_notional": 0.0},
            {"datetime": "2026-04-03 15:00:00", "equity": 1_000_050.0, "position_notional": 0.0},
            {"datetime": "2026-04-05 15:00:00", "equity": 1_000_200.0, "position_notional": 0.0},
        ])
        price_df = pd.DataFrame(index=pd.to_datetime([
            "2026-04-01 09:00:00",
            "2026-04-03 15:00:00",
            "2026-04-05 15:00:00",
        ]))
        match_df = pd.DataFrame([
            {"symbol": "au", "close_time": "2026-04-03 10:00:00", "net_pnl": -50.0},
            {"symbol": "au", "close_time": "2026-04-05 10:00:00", "net_pnl": 150.0},
        ])

        analyzer = StrategyAnalyzer(
            trades=[],
            price_df=price_df,
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="tick",
            strategy_name="TestStrategy",
            equity_df=equity_df,
        )

        metrics = analyzer._calc_single_metrics(match_df, "MULTI", "TestStrategy", is_total=True)

        self.assertEqual(metrics["交易次数"], 2)
        self.assertEqual(metrics["成交日数"], 2)
        self.assertEqual(metrics["行情日数"], 3)
        self.assertNotIn("交易日数", metrics)

    def test_fund_flow_commission_accumulates_by_trade_time(self):
        equity_df = pd.DataFrame([
            {"datetime": "2026-05-07 09:00:00", "equity": 1_000_000.0, "position_notional": 0.0},
            {"datetime": "2026-05-07 09:00:01", "equity": 1_000_000.0, "position_notional": 0.0},
            {"datetime": "2026-05-07 21:00:49", "equity": 999_980.0, "position_notional": 0.0},
            {"datetime": "2026-05-07 21:00:50", "equity": 999_950.0, "position_notional": 0.0},
        ])
        price_df = pd.DataFrame(index=pd.to_datetime(equity_df["datetime"]))
        trade = Trade(
            symbol="ag",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=19990.0,
            trade_time=datetime(2026, 5, 7, 21, 0, 49),
            commission=29.97,
            slippage_cost=0.0,
            order_id="test-order",
        )

        analyzer = StrategyAnalyzer(
            trades=[trade],
            price_df=price_df,
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="tick",
            strategy_name="TestStrategy",
            equity_df=equity_df,
        )

        fund_flow = analyzer.get_fund_flow_df()
        commission_col = fund_flow.columns[-1]

        self.assertEqual(float(fund_flow.iloc[0][commission_col]), 0.0)
        self.assertEqual(float(fund_flow.iloc[1][commission_col]), 0.0)
        self.assertEqual(float(fund_flow.iloc[2][commission_col]), 29.97)
        self.assertEqual(float(fund_flow.iloc[3][commission_col]), 29.97)


if __name__ == "__main__":
    unittest.main()
