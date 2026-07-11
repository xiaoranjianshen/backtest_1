# -*- coding: utf-8 -*-
import unittest

import pandas as pd

from data_feed.trading_calendar import infer_trading_dates


class TradingCalendarTest(unittest.TestCase):
    def test_standalone_saturday_early_bar_maps_to_monday(self):
        result = infer_trading_dates(pd.Series(pd.to_datetime(['2026-07-11 01:00:00'])))

        self.assertEqual(result.iloc[0], pd.Timestamp('2026-07-13'))

    def test_night_and_early_morning_map_to_next_available_day_session(self):
        timestamps = pd.Series(pd.to_datetime([
            "2026-07-10 21:00:00",  # Friday night belongs to Monday.
            "2026-07-11 01:00:00",
            "2026-07-13 09:00:00",
            "2026-07-13 14:59:00",
        ]))

        trading_dates = infer_trading_dates(timestamps)

        self.assertEqual(trading_dates.tolist(), [pd.Timestamp("2026-07-13")] * 4)

    def test_regular_weeknight_maps_to_next_calendar_day(self):
        timestamps = pd.Series(pd.to_datetime([
            "2026-07-13 21:00:00",
            "2026-07-14 01:00:00",
            "2026-07-14 09:00:00",
        ]))

        self.assertEqual(infer_trading_dates(timestamps).tolist(), [pd.Timestamp("2026-07-14")] * 3)


if __name__ == "__main__":
    unittest.main()
