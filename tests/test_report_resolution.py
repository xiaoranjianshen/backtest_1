# -*- coding: utf-8 -*-
import unittest

import pandas as pd

from analyzer.performance import StrategyAnalyzer


class ReportResolutionTest(unittest.TestCase):
    def _build_analyzer(self, freq: str, days: int):
        index = pd.date_range("2023-01-01 09:00:00", periods=days * 24, freq="h")
        equity_df = pd.DataFrame({
            "datetime": index,
            "equity": [1_000_000 + idx for idx in range(len(index))],
        })
        price_df = pd.DataFrame({"rb": [100 + idx * 0.01 for idx in range(len(index))]}, index=index)
        return StrategyAnalyzer(
            trades=[],
            price_df=price_df,
            initial_capital=1_000_000,
            symbol="MULTI",
            freq=freq,
            strategy_name="ReportResolutionTest",
            equity_df=equity_df,
        )

    def test_long_intraday_report_uses_daily_resolution(self):
        analyzer = self._build_analyzer("1m", 400)

        x_values, y_values = analyzer._get_equity_series()

        self.assertLess(len(x_values), 500)
        self.assertLess(len(x_values), 400 * 24)
        self.assertEqual(y_values[-1], 1_000_000 + 400 * 24 - 1)

    def test_short_intraday_report_keeps_source_resolution(self):
        analyzer = self._build_analyzer("1m", 30)

        x_values, _ = analyzer._get_equity_series()

        self.assertEqual(len(x_values), 30 * 24)

    def test_dense_intraday_report_uses_daily_resolution_even_before_one_year(self):
        index = pd.date_range("2023-01-01 09:00:00", periods=100_001, freq="min")
        equity_df = pd.DataFrame({
            "datetime": index,
            "equity": [1_000_000 + idx for idx in range(len(index))],
        })
        analyzer = StrategyAnalyzer(
            trades=[],
            price_df=pd.DataFrame({"rb": [100.0] * len(index)}, index=index),
            initial_capital=1_000_000,
            symbol="MULTI",
            freq="1m",
            strategy_name="ReportResolutionTest",
            equity_df=equity_df,
        )

        x_values, _ = analyzer._get_equity_series()

        self.assertLess(len(x_values), 100_001)

    def test_daily_report_keeps_source_resolution(self):
        analyzer = self._build_analyzer("1d", 400)

        x_values, _ = analyzer._get_equity_series()

        self.assertEqual(len(x_values), 400 * 24)


if __name__ == "__main__":
    unittest.main()
