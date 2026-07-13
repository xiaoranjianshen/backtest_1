# -*- coding: utf-8 -*-
import unittest

import pandas as pd

from backtest_engine import _build_equity_record, _describe_margin_rates


class BacktestEngineParamsTest(unittest.TestCase):
    def test_margin_rates_are_deduplicated_by_product(self):
        text = _describe_margin_rates(["c2507", "c2509", "CF507", "CF509", "AP510"])

        self.assertEqual(text, "C:9%, CF:7%, AP:12%")

    def test_equity_record_keeps_actual_frozen_margin(self):
        class StubAccount:
            positions = {}
            frozen_margin = 123_456.0

            @staticmethod
            def get_total_equity(_current_prices):
                return 1_000_000.0

        record = _build_equity_record(
            pd.Timestamp("2026-05-07 09:00:00"),
            pd.Timestamp("2026-05-07"),
            StubAccount(),
            {},
        )

        self.assertEqual(record["margin_used"], 123_456.0)


if __name__ == "__main__":
    unittest.main()
