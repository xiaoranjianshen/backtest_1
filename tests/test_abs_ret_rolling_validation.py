# -*- coding: utf-8 -*-
import tempfile
import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from broker.match_engine import MatchEngine
from portfolio.account import Account
from strategy.custom.abs_ret_rolling_validation import AbsRetRollingValidationStrategy


class AbsRetRollingValidationTest(unittest.TestCase):
    def test_night_session_signal_maps_to_next_trading_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            signal_path = tmp_path / "signals.csv"
            validation_path = tmp_path / "validation.csv"

            validation_path.write_text(
                "experiment,product,weighted_hit_rate,rows\n"
                "hybrid_product,c,0.70,100\n"
                "hybrid_product,CF,0.70,100\n",
                encoding="utf-8",
            )
            signal_path.write_text(
                "symbol,product,start_datetime,end_datetime,prob_up,edge,direction,experiment\n"
                "c2507,c,2025-07-04 13:30:00,2025-07-04 14:00:00,0.62,0.12,1,hybrid_product\n"
                "CF509,CF,2025-07-04 21:02:00,2025-07-04 21:12:00,0.38,0.12,-1,hybrid_product\n",
                encoding="utf-8",
            )

            account = Account(initial_capital=1_000_000)
            strategy = AbsRetRollingValidationStrategy(
                broker=MatchEngine(account),
                account=account,
                target_symbols=["c2507", "cf509"],
                prediction_mode="replay_csv",
                signal_path=signal_path,
                validation_path=validation_path,
                validation_mode="aggregate",
                edge_threshold_mode="none",
                trading_calendar=[
                    pd.Timestamp("2025-07-04"),
                    pd.Timestamp("2025-07-07"),
                    pd.Timestamp("2025-07-08"),
                ],
            )

        self.assertIn(pd.Timestamp("2025-07-04"), strategy.signals_by_time)
        self.assertIn(pd.Timestamp("2025-07-07"), strategy.signals_by_time)

        day_rows = strategy.signal_frame.loc[strategy.signals_by_time[pd.Timestamp("2025-07-04")]]
        night_rows = strategy.signal_frame.loc[strategy.signals_by_time[pd.Timestamp("2025-07-07")]]

        self.assertEqual(day_rows["symbol_key"].tolist(), ["c2507"])
        self.assertEqual(night_rows["symbol_key"].tolist(), ["cf509"])


if __name__ == "__main__":
    unittest.main()
