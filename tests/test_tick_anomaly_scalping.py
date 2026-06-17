# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
import unittest

from broker.match_engine import MatchEngine
from portfolio.account import Account
from strategy.custom.tick_anomaly_scalping import EntryState, TickAnomalyScalpingStrategy


def _tick_bar(price: float, fresh: bool = True) -> dict:
    return {
        "close": price,
        "last_price": price,
        "mid_price": price,
        "bid_price_1": price - 0.01,
        "ask_price_1": price + 0.01,
        "spread": 0.02,
        "is_fresh": fresh,
    }


class TickAnomalyScalpingStrategyTest(unittest.TestCase):
    def _strategy(self, **kwargs) -> TickAnomalyScalpingStrategy:
        account = Account(initial_capital=1_000_000.0)
        broker = MatchEngine(account)
        params = {
            "target_symbols": ["au"],
            "shock_window_seconds": 1.0,
            "lookback_days": 1.0,
            "tail_prob": 0.1,
            "min_move_bps": 2.0,
            "min_history_samples": 1000,
            "directional_ratio": 1.0,
            "max_spread_ticks": 3.0,
            "hold_seconds": 5.0,
            "take_profit_ticks": 2.0,
            "stop_loss_ticks": 4.0,
            "cooldown_seconds": 0.0,
            "require_history_ready": False,
        }
        params.update(kwargs)
        return TickAnomalyScalpingStrategy(broker=broker, account=account, symbol="multi", **params)

    def test_fade_mode_shorts_after_upward_tick_shock(self):
        strategy = self._strategy(scalp_mode="fade")
        t0 = datetime(2026, 5, 15, 9, 0, 0)
        strategy.current_time = t0
        strategy.generate_signals({"au": _tick_bar(600.0)})

        strategy.current_time = t0 + timedelta(seconds=1)
        signals = strategy.generate_signals({"au": _tick_bar(600.5)})

        self.assertEqual(signals["au"]["signal"], -1)
        self.assertEqual(signals["au"]["position_mode"], "target")

    def test_follow_mode_buys_after_upward_tick_shock(self):
        strategy = self._strategy(scalp_mode="follow")
        t0 = datetime(2026, 5, 15, 9, 0, 0)
        strategy.current_time = t0
        strategy.generate_signals({"au": _tick_bar(600.0)})

        strategy.current_time = t0 + timedelta(seconds=1)
        signals = strategy.generate_signals({"au": _tick_bar(600.5)})

        self.assertEqual(signals["au"]["signal"], 1)
        self.assertEqual(signals["au"]["position_mode"], "target")

    def test_reversal_mode_waits_for_confirmed_retrace(self):
        strategy = self._strategy(
            scalp_mode="reversal",
            reversal_confirm_seconds=2.0,
            reversal_retrace_ratio=0.4,
            reversal_min_retrace_ticks=1.0,
        )
        t0 = datetime(2026, 5, 15, 9, 0, 0)
        strategy.current_time = t0
        strategy.generate_signals({"au": _tick_bar(600.0)})

        strategy.current_time = t0 + timedelta(seconds=1)
        pending = strategy.generate_signals({"au": _tick_bar(600.5)})
        self.assertIsNone(pending["au"]["signal"])
        self.assertEqual(pending["au"]["reason"], "pending_reversal")

        strategy.current_time = t0 + timedelta(seconds=1, milliseconds=500)
        signals = strategy.generate_signals({"au": _tick_bar(600.25)})

        self.assertEqual(signals["au"]["signal"], -1)
        self.assertEqual(signals["au"]["position_mode"], "target")
        self.assertEqual(signals["au"]["reason"], "confirmed_reversal_entry")

    def test_exit_after_holding_seconds(self):
        strategy = self._strategy()
        t0 = datetime(2026, 5, 15, 9, 0, 0)
        strategy.account.positions["au_LONG"] = {
            "yd_volume": 1,
            "td_volume": 0,
            "avg_price": 600.0,
            "frozen_margin": 0.0,
        }
        strategy.entry_state["au"] = EntryState(direction=1, price=600.0, time=t0)

        strategy.current_time = t0 + timedelta(seconds=6)
        signals = strategy.generate_signals({"au": _tick_bar(600.0)})

        self.assertEqual(signals["au"]["signal"], 0)
        self.assertEqual(signals["au"]["position_mode"], "flat")
        self.assertEqual(signals["au"]["reason"], "time_exit")

    def test_stale_tick_does_not_trigger_entry(self):
        strategy = self._strategy(scalp_mode="fade")
        t0 = datetime(2026, 5, 15, 9, 0, 0)
        strategy.current_time = t0
        strategy.generate_signals({"au": _tick_bar(600.0)})

        strategy.current_time = t0 + timedelta(seconds=1)
        signals = strategy.generate_signals({"au": _tick_bar(602.0, fresh=False)})

        self.assertIsNone(signals["au"]["signal"])
        self.assertEqual(signals["au"]["reason"], "stale_tick")


if __name__ == "__main__":
    unittest.main()
