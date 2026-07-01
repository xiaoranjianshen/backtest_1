# -*- coding: utf-8 -*-
import sys
import tempfile
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

    def test_zscore_reversal_config_builds_strategy_kwargs(self):
        args = build_run_arguments({
            "strategy": "zscore_reversal",
            "symbols": "rb,hc,i",
            "start_date": "2021-01-01",
            "end_date": "2022-01-01",
            "lookback": 10,
            "entry_z": 2.1,
            "first_exit_z": 0.0,
            "final_exit_z": 1.0,
            "sizing_mode": "available_pct",
            "sizing_value": 0.03,
        })

        self.assertEqual(args["strategy_class"].__module__, "strategy.custom.zscore_reversal")
        self.assertEqual(args["strategy_class"].__name__, "ZScoreReversalStrategy")
        self.assertEqual(args["symbols_input"], ["rb", "hc", "i"])
        self.assertEqual(args["strategy_kwargs"]["target_symbols"], ["rb", "hc", "i"])
        self.assertEqual(args["strategy_kwargs"]["lookback"], 10)
        self.assertEqual(args["strategy_kwargs"]["entry_z"], 2.1)
        self.assertEqual(args["strategy_kwargs"]["sizing"]["mode"], "available_pct")

    def test_opponent_order_type_requires_tick_frequency(self):
        with self.assertRaisesRegex(ValueError, "only supported for tick"):
            build_run_arguments({
                "strategy": "general_multi_ma",
                "symbols": "rb,hc",
                "freq": "1d",
                "data_type": "main",
                "order_type": "opponent",
            })

    def test_tick_opponent_order_type_is_allowed(self):
        args = build_run_arguments({
            "strategy": "general_multi_ma",
            "symbols": "rb,hc",
            "freq": "tick",
            "data_type": "main",
            "order_type": "opponent",
            "price_field": "mid_price",
        })

        self.assertEqual(args["strategy_kwargs"]["execution"]["order_type"], "opponent")
        self.assertEqual(args["strategy_kwargs"]["execution"]["price_field"], "mid_price")

    def test_tick_anomaly_scalping_forces_tick_data_but_respects_execution_config(self):
        args = build_run_arguments({
            "strategy": "tick_anomaly_scalping",
            "symbols": "au",
            "freq": "1d",
            "data_type": "main",
            "order_type": "limit",
            "price_field": "last_price",
            "limit_mode": "better_ticks",
            "limit_ticks": 2.0,
            "scalp_mode": "follow",
        })

        self.assertEqual(args["strategy_class"].__module__, "strategy.custom.tick_anomaly_scalping")
        self.assertEqual(args["strategy_class"].__name__, "TickAnomalyScalpingStrategy")
        self.assertEqual(args["symbols_input"], ["au"])
        self.assertEqual(args["freq"], "tick")
        self.assertEqual(args["data_type"], "main")
        self.assertEqual(args["strategy_kwargs"]["scalp_mode"], "follow")
        self.assertEqual(args["strategy_kwargs"]["execution"]["order_type"], "limit")
        self.assertEqual(args["strategy_kwargs"]["execution"]["price_field"], "last_price")
        self.assertEqual(args["strategy_kwargs"]["execution"]["limit_mode"], "better_ticks")
        self.assertEqual(args["strategy_kwargs"]["execution"]["ticks"], 2.0)

    def test_abs_ret_rolling_validation_expands_products_to_contract_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            validation_path = tmp_path / "validation.csv"
            signal_path = tmp_path / "signals.csv"
            validation_path.write_text(
                "experiment,product,weighted_hit_rate,rows\n"
                "hybrid_product,c,0.70,100\n"
                "hybrid_product,CF,0.61,100\n"
                "hybrid_product,AP,0.55,100\n",
                encoding="utf-8",
            )
            signal_path.write_text(
                "symbol,product,experiment\n"
                "c2507,c,hybrid_product\n"
                "CF507,CF,hybrid_product\n"
                "AP507,AP,hybrid_product\n",
                encoding="utf-8",
            )

            args = build_run_arguments({
                "strategy": "abs_ret_rolling_validation",
                "symbols": "c,CF",
                "start_date": "2025-07-01",
                "end_date": "2025-07-03",
                "prediction_mode": "replay_csv",
                "signal_path": str(signal_path),
                "validation_path": str(validation_path),
                "model_name": "hybrid_product",
                "min_validation_hit_rate": 0.60,
                "max_total_margin_pct": 0.30,
            })

        self.assertEqual(args["strategy_class"].__module__, "strategy.custom.abs_ret_rolling_validation")
        self.assertEqual(args["strategy_class"].__name__, "AbsRetRollingValidationStrategy")
        self.assertEqual(args["freq"], "1d")
        self.assertEqual(args["data_type"], "all")
        self.assertEqual(args["symbols_input"], ["c2507", "cf507"])
        self.assertEqual(args["strategy_kwargs"]["target_symbols"], ["c2507", "cf507"])
        self.assertEqual(args["strategy_kwargs"]["signal_frequency"], "daily")
        self.assertEqual(args["strategy_kwargs"]["daily_signal_policy"], "strongest")
        self.assertEqual(args["strategy_kwargs"]["max_total_margin_pct"], 0.30)
        self.assertEqual(args["strategy_kwargs"]["sizing"]["min_volume"], 0)
        self.assertEqual(args["strategy_kwargs"]["min_signal_confidence"], 0.60)

    def test_abs_ret_blank_selection_can_use_all_prediction_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            validation_path = tmp_path / "validation.csv"
            signal_path = tmp_path / "signals.csv"
            monthly_path = tmp_path / "monthly.csv"
            validation_path.write_text(
                "experiment,product,weighted_hit_rate,rows\n"
                "hybrid_product,c,0.70,100\n"
                "hybrid_product,AP,0.55,100\n",
                encoding="utf-8",
            )
            monthly_path.write_text(
                "experiment,month,product,slice,all_rows,rows,hit_rate,mean_signed_ret_pct,long_share\n"
                "hybrid_product,2025-07,c,product_top10,100,50,0.70,0.01,0.5\n",
                encoding="utf-8",
            )
            signal_path.write_text(
                "symbol,product,experiment\n"
                "c2507,c,hybrid_product\n"
                "AP507,AP,hybrid_product\n",
                encoding="utf-8",
            )

            args = build_run_arguments({
                "strategy": "abs_ret_rolling_validation",
                "symbols": "",
                "prediction_mode": "replay_csv",
                "signal_path": str(signal_path),
                "validation_path": str(validation_path),
                "monthly_validation_path": str(monthly_path),
                "validation_mode": "monthly_prior",
                "absret_universe_mode": "all_predictions",
            })

        self.assertEqual(args["symbols_input"], ["ap507", "c2507"])

    def test_abs_ret_blank_selection_can_use_validated_products_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            validation_path = tmp_path / "validation.csv"
            signal_path = tmp_path / "signals.csv"
            validation_path.write_text(
                "experiment,product,weighted_hit_rate,rows\n"
                "hybrid_product,c,0.70,100\n"
                "hybrid_product,AP,0.55,100\n",
                encoding="utf-8",
            )
            signal_path.write_text(
                "symbol,product,experiment\n"
                "c2507,c,hybrid_product\n"
                "AP507,AP,hybrid_product\n",
                encoding="utf-8",
            )

            args = build_run_arguments({
                "strategy": "abs_ret_rolling_validation",
                "symbols": "",
                "prediction_mode": "replay_csv",
                "signal_path": str(signal_path),
                "validation_path": str(validation_path),
                "validation_mode": "aggregate",
                "absret_universe_mode": "validated_products",
            })

        self.assertEqual(args["symbols_input"], ["c2507"])


if __name__ == "__main__":
    unittest.main()
