# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui.run_from_config import build_run_arguments


class UiConfigIntegrationTest(unittest.TestCase):
    def test_general_multi_ma_config_builds_latest_strategy_kwargs(self):
        args = build_run_arguments({
            "strategy": "general_multi_ma",
            "symbols": "rb,hc",
            "start_date": "2021-01-01",
            "end_date": "2022-01-01",
            "freq": "1d",
            "data_type": "main",
            "initial_capital": 5_000_000,
            "fast_window": 10,
            "slow_window": 30,
            "sizing_mode": "equity_pct",
            "sizing_value": 0.03,
            "min_volume": 1,
            "max_volume": None,
            "order_type": "market",
            "slippage_ticks": 0.5,
            "close_pct": 1.0,
            "allow_reverse": True,
            "respect_pending_orders": True,
        })

        self.assertEqual(args["strategy_class"].__name__, "GeneralMultiMAStrategy")
        self.assertEqual(args["symbols_input"], ["rb", "hc"])
        self.assertEqual(args["strategy_kwargs"]["target_symbols"], ["rb", "hc"])
        self.assertEqual(args["strategy_kwargs"]["sizing"]["mode"], "equity_pct")
        self.assertEqual(args["strategy_kwargs"]["sizing"]["value"], 0.03)
        self.assertEqual(args["strategy_kwargs"]["execution"]["order_type"], "market")
        self.assertEqual(args["strategy_kwargs"]["execution"]["slippage_ticks"], 0.5)
        self.assertEqual(args["strategy_kwargs"]["exit"]["close_pct"], 1.0)

    def test_dual_ma_uses_custom_strategy_module(self):
        args = build_run_arguments({
            "strategy": "dual_ma",
            "symbols": "rb",
            "start_date": "2021-01-01",
            "end_date": "2022-01-01",
        })

        self.assertEqual(args["strategy_class"].__module__, "strategy.custom.dual_ma")
        self.assertEqual(args["strategy_class"].__name__, "DualMAStrategy")


if __name__ == "__main__":
    unittest.main()
