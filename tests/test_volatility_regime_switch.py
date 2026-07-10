# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pandas as pd

from run_scripts.run_volatility_regime_switch import DEFAULT_SYMBOLS, export_analysis_bundle, parse_args
from data_feed.timeframe import AggregatedBar as _AggBar, IntradayBarAggregator as _MinuteAggregator

from strategy.custom.volatility_regime_switch import (
    _MacdBoxTracker,
    _MacdPriceBox,
    VolatilityRegimeSwitchStrategy,
    _allocate_margin_weights,
    _percentile_rank,
    _rolling_volatility_series,
    _std,
)


class VolatilityRegimeSwitchHelpersTest(unittest.TestCase):
    def test_run_script_defaults_match_validated_candidate(self):
        with patch("sys.argv", ["run_volatility_regime_switch.py"]):
            args = parse_args()

        self.assertEqual(args.symbols, DEFAULT_SYMBOLS)
        self.assertEqual(args.vol_window_days, 40)
        self.assertEqual(args.regime_threshold, 0.90)
        self.assertEqual(args.trend_regime_threshold, 0.30)
        self.assertEqual(args.reversion_minutes, 60)
        self.assertEqual(args.trend_minutes, 120)
        self.assertEqual(args.selection_count, 15)
        self.assertEqual(args.rebalance_frequency, "daily")
        self.assertEqual(args.reversion_max_hold_bars, 48)
        self.assertFalse(args.use_performance_selection)
        self.assertTrue(args.no_short)
        self.assertEqual(args.trend_exit_mode, "macd_box")
        self.assertEqual(args.trend_trailing_atr_mult, 3.0)
        self.assertEqual(args.trend_macd_box_volume_mult, 0.0)

    def test_macd_box_is_confirmed_only_after_a_completed_shorter_histogram_bar(self):
        tracker = _MacdBoxTracker(fast_window=2, slow_window=3, signal_window=2)
        prices = [100, 100, 100, 101, 103, 106, 108]
        snapshots = []
        for index, price in enumerate(prices, start=1):
            bar = _AggBar(
                start=datetime(2026, 1, 1) + timedelta(hours=index),
                end=datetime(2026, 1, 1) + timedelta(hours=index),
                open=price - 0.2,
                high=price + 1.0,
                low=price - 1.0,
                close=float(price),
                volume=100.0,
            )
            snapshots.append(tracker.update(bar, index))

        self.assertIsNone(snapshots[5]["confirmed_box"])
        box = snapshots[6]["confirmed_box"]
        self.assertIsNotNone(box)
        self.assertEqual(box.direction, 1)
        self.assertEqual(box.source_bar_index, 6)
        self.assertEqual(box.confirmed_bar_index, 7)
        self.assertEqual(box.high, 107.0)
        self.assertEqual(box.low, 105.0)

    def test_macd_box_exit_ignores_boxes_not_confirmed_after_entry(self):
        strategy = VolatilityRegimeSwitchStrategy.__new__(VolatilityRegimeSwitchStrategy)
        box = _MacdPriceBox(
            direction=1,
            high=107.0,
            low=105.0,
            histogram=0.34,
            source_start=datetime(2026, 1, 1, 10, 0),
            source_end=datetime(2026, 1, 1, 11, 59),
            source_bar_index=6,
            confirmed_bar_index=7,
        )
        strategy.trend_exit_snapshots = {"rb": {"ready": True, "top_box": box, "macd": 1.0, "macd_signal": 0.8, "macd_histogram": 0.4}}
        strategy.trend_history = {"rb": []}
        strategy.trend_macd_box_volume_mult = 0.0
        strategy.trend_macd_box_volume_window = 20
        strategy.entry_bar_count = {"rb": 7}
        break_bar = _AggBar(
            start=datetime(2026, 1, 1, 14, 0),
            end=datetime(2026, 1, 1, 15, 59),
            open=106.0,
            high=106.5,
            low=103.5,
            close=104.0,
            volume=100.0,
        )

        self.assertIsNone(strategy._trend_macd_box_exit("rb", break_bar, 1, {}))
        strategy.entry_bar_count["rb"] = 6
        signal = strategy._trend_macd_box_exit("rb", break_bar, 1, {})
        self.assertEqual(signal["signal"], 0)
        self.assertEqual(signal["reason"], "trend_macd_top_box_break_exit")

    def test_zscore_reversion_uses_common_max_hold_exit(self):
        strategy = VolatilityRegimeSwitchStrategy.__new__(VolatilityRegimeSwitchStrategy)
        strategy.reversion_model = "zscore"
        strategy.reversion_max_hold_bars = 12
        strategy.entry_bar_count = {"rb": 4}
        strategy.reversion_bar_count = {"rb": 16}
        strategy.get_net_position = lambda symbol: 3
        strategy._reversion_zscore = lambda *args: {
            "signal": None,
            "reason": "reversion_hold",
            "metrics": {"zscore": -0.8},
        }
        bar = _AggBar(
            start=datetime(2026, 1, 1, 9, 0),
            end=datetime(2026, 1, 1, 9, 59),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1.0,
        )

        signal = strategy._reversion_signal("rb", bar, SimpleNamespace())

        self.assertEqual(signal["signal"], 0)
        self.assertEqual(signal["position_mode"], "flat")
        self.assertEqual(signal["reason"], "reversion_time_exit")
        self.assertEqual(signal["metrics"]["holding_reversion_bars"], 12)

    def test_analysis_bundle_exports_compact_daily_equity(self):
        analyzer = SimpleNamespace(
            match_df=pd.DataFrame([{"symbol": "rb", "net_pnl": 100.0}]),
            equity_df=pd.DataFrame([
                {"datetime": "2026-07-10 21:00:00", "equity": 1_000_000.0},
                {"datetime": "2026-07-11 01:00:00", "equity": 1_000_100.0},
                {"datetime": "2026-07-11 09:00:00", "equity": 1_000_200.0},
            ]),
            metrics_list=[{"symbol": "MULTI", "total_return": "1.0%"}],
            selection_records=[{"trade_date": "2026-07-10", "symbol": "rb"}],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            export_analysis_bundle(analyzer, temp_dir)
            daily_equity = pd.read_csv(Path(temp_dir) / "daily_equity.csv")

            self.assertEqual(len(daily_equity), 2)
            self.assertEqual(float(daily_equity.iloc[0]["equity"]), 1_000_100.0)
            self.assertTrue((Path(temp_dir) / "matched_trades.csv").exists())
            self.assertTrue((Path(temp_dir) / "metrics.csv").exists())
            self.assertTrue((Path(temp_dir) / "selection_records.csv").exists())

    def test_margin_slots_remain_cash_when_too_few_symbols_qualify(self):
        sparse = _allocate_margin_weights([1.0, 1.0, 1.0], planned_count=8, total_margin_target=0.30)
        full = _allocate_margin_weights([1.0] * 8, planned_count=8, total_margin_target=0.30)
        overweight = _allocate_margin_weights([1.2] * 8, planned_count=8, total_margin_target=0.30)

        self.assertAlmostEqual(sum(sparse), 0.1125)
        self.assertTrue(all(abs(item - 0.0375) < 1e-12 for item in sparse))
        self.assertAlmostEqual(sum(full), 0.30)
        self.assertAlmostEqual(sum(overweight), 0.30)

    def test_minute_aggregator_returns_completed_previous_bucket(self):
        agg = _MinuteAggregator(30)
        first = datetime(2026, 1, 1, 9, 0)
        self.assertIsNone(agg.update("rb", first, {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 10}))
        self.assertIsNone(agg.update("rb", first + timedelta(minutes=1), {"open": 100, "high": 102, "low": 98, "close": 101, "volume": 5}))
        for minute in range(2, 30):
            self.assertIsNone(
                agg.update(
                    "rb",
                    first + timedelta(minutes=minute),
                    {"open": 101, "high": 101, "low": 101, "close": 101, "volume": 1},
                )
            )

        completed = agg.update("rb", first + timedelta(minutes=30), {"open": 103, "high": 104, "low": 102, "close": 103, "volume": 7})

        self.assertIsNotNone(completed)
        self.assertEqual(completed.start, first)
        self.assertEqual(completed.end, first + timedelta(minutes=29))
        self.assertEqual(completed.open, 100)
        self.assertEqual(completed.high, 102)
        self.assertEqual(completed.low, 98)
        self.assertEqual(completed.close, 101)
        self.assertEqual(completed.volume, 43)

    def test_two_hour_aggregator_does_not_degrade_to_hourly_buckets(self):
        agg = _MinuteAggregator(120)
        first = datetime(2026, 1, 1, 9, 0)

        timestamps = [first + timedelta(minutes=minute) for minute in range(75)]
        timestamps += [datetime(2026, 1, 1, 10, 30) + timedelta(minutes=minute) for minute in range(45)]
        for idx, timestamp in enumerate(timestamps):
            self.assertIsNone(agg.update("rb", timestamp, {"close": 100 + idx}))

        completed = agg.update("rb", datetime(2026, 1, 1, 11, 15), {"close": 104})

        self.assertIsNotNone(completed)
        self.assertEqual(completed.start, first)
        self.assertEqual(completed.end, datetime(2026, 1, 1, 11, 14))
        self.assertEqual(completed.close, 219)

    def test_two_hour_night_bar_is_anchored_at_night_open(self):
        agg = _MinuteAggregator(120)
        first = datetime(2026, 1, 1, 21, 0)

        for minute in range(120):
            self.assertIsNone(agg.update("rb", first + timedelta(minutes=minute), {"close": 100 + minute}))
        completed = agg.update("rb", datetime(2026, 1, 1, 23, 0), {"close": 102})

        self.assertIsNotNone(completed)
        self.assertEqual(completed.start, first)
        self.assertEqual(completed.end, datetime(2026, 1, 1, 22, 59))

    def test_two_hour_night_bar_remains_continuous_across_midnight(self):
        agg = _MinuteAggregator(120)
        first = datetime(2026, 1, 1, 23, 0)

        for minute in range(120):
            self.assertIsNone(agg.update("au", first + timedelta(minutes=minute), {"close": 100 + minute}))
        completed = agg.update("au", datetime(2026, 1, 2, 1, 0), {"close": 220})

        self.assertIsNotNone(completed)
        self.assertEqual(completed.start, first)
        self.assertEqual(completed.end, datetime(2026, 1, 2, 0, 59))

    def test_financial_day_bars_are_anchored_at_0930(self):
        agg = _MinuteAggregator(60)
        first = datetime(2026, 1, 1, 9, 30)

        for minute in range(60):
            self.assertIsNone(agg.update("if", first + timedelta(minutes=minute), {"close": 100 + minute}))
        completed = agg.update("if", datetime(2026, 1, 1, 10, 30), {"close": 102})

        self.assertIsNotNone(completed)
        self.assertEqual(completed.start, first)
        self.assertEqual(completed.end, datetime(2026, 1, 1, 10, 29))

    def test_incomplete_session_tail_is_not_emitted_as_full_bar(self):
        agg = _MinuteAggregator(30)
        tail_start = datetime(2026, 1, 1, 14, 45)
        for minute in range(15):
            self.assertIsNone(agg.update("rb", tail_start + timedelta(minutes=minute), {"close": 100}))

        self.assertIsNone(agg.update("rb", datetime(2026, 1, 1, 21, 0), {"close": 101}))

    def test_stale_aligned_rows_do_not_complete_a_bar(self):
        agg = _MinuteAggregator(30)
        first = datetime(2026, 1, 1, 9, 0)
        for minute in range(15):
            self.assertIsNone(
                agg.update("rb", first + timedelta(minutes=minute), {"close": 100, "is_fresh": True})
            )
        for minute in range(15, 30):
            self.assertIsNone(
                agg.update("rb", first + timedelta(minutes=minute), {"close": 100, "is_fresh": False})
            )

        self.assertIsNone(
            agg.update("rb", first + timedelta(minutes=30), {"close": 101, "is_fresh": True})
        )

    def test_std_uses_population_definition_for_stable_thresholds(self):
        self.assertAlmostEqual(_std([1.0, 2.0, 3.0]), (2.0 / 3.0) ** 0.5)
        self.assertEqual(_std([1.0]), 0.0)

    def test_rolling_volatility_series_uses_trailing_returns(self):
        closes = [100, 101, 99, 102, 101, 104]
        vols = _rolling_volatility_series(closes, window=3)

        self.assertEqual(len(vols), 3)
        self.assertTrue(all(item > 0 for item in vols))

    def test_percentile_rank_uses_symbol_own_history(self):
        observations = [0.01, 0.02, 0.03, 0.04]

        self.assertAlmostEqual(_percentile_rank(0.035, observations), 0.75)
        self.assertAlmostEqual(_percentile_rank(0.005, observations), 0.0)
        self.assertAlmostEqual(_percentile_rank(0.05, observations), 1.0)


if __name__ == "__main__":
    unittest.main()
