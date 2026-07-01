# -*- coding: utf-8 -*-
import unittest

from backtest_engine import _describe_margin_rates


class BacktestEngineParamsTest(unittest.TestCase):
    def test_margin_rates_are_deduplicated_by_product(self):
        text = _describe_margin_rates(["c2507", "c2509", "CF507", "CF509", "AP510"])

        self.assertEqual(text, "C:9%, CF:7%, AP:12%")


if __name__ == "__main__":
    unittest.main()
