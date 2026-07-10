# -*- coding: utf-8 -*-
import unittest
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import extract_bar_data
from broker.order import Direction
from broker.rollover import MainContractRollover
from data_feed.ch_loader import ClickHouseLoader


class IntradayRolloverMarkTests(unittest.TestCase):
    def setUp(self):
        # 该方法只处理 DataFrame，不需要建立数据库连接。
        self.loader = object.__new__(ClickHouseLoader)

    def test_night_session_rollover_is_marked_at_2100_with_real_contract_prices(self):
        frame = pd.DataFrame([
            {
                'symbol': 'KQ.m@SHFE.al', 'datetime': '2023-06-20 14:59:00',
                'open': 18540.0, 'high': 18550.0, 'low': 18540.0, 'close': 18545.0,
                'volume': 1, 'oi': 1, 'month_change': 0,
                'underlying_symbol': 'al2307', 'adjust_factor': 1.0,
            },
            {
                'symbol': 'KQ.m@SHFE.al', 'datetime': '2023-06-20 21:00:00',
                'open': 18235.0, 'high': 18240.0, 'low': 18230.0, 'close': 18238.0,
                'volume': 1, 'oi': 1, 'month_change': 1,
                'underlying_symbol': 'al2308', 'adjust_factor': 1.0,
            },
            {
                'symbol': 'KQ.m@SHFE.al', 'datetime': '2023-06-21 00:00:00',
                'open': 18260.0, 'high': 18265.0, 'low': 18255.0, 'close': 18260.0,
                'volume': 1, 'oi': 1, 'month_change': 1,
                'underlying_symbol': 'al2308', 'adjust_factor': 1.0,
            },
        ])

        marked = self.loader._attach_intraday_rollover_marks(frame)
        events = marked.loc[marked['month_change'].eq(1)]

        self.assertEqual(len(events), 1)
        event = events.iloc[0]
        self.assertEqual(event['datetime'], pd.Timestamp('2023-06-20 21:00:00'))
        self.assertEqual(event['previous_underlying_symbol'], 'al2307')
        self.assertEqual(event['underlying_symbol'], 'al2308')
        self.assertEqual(event['roll_old_close'], 18545.0)
        self.assertEqual(event['roll_new_open'], 18235.0)

    def test_day_session_rollover_is_marked_on_first_bar(self):
        frame = pd.DataFrame([
            {
                'symbol': 'KQ.m@CZCE.RM', 'datetime': '2024-01-15 14:59:00',
                'open': 3447.0, 'high': 3449.0, 'low': 3447.0, 'close': 3448.0,
                'volume': 1, 'oi': 1, 'month_change': 0,
                'underlying_symbol': 'RM403', 'adjust_factor': 1.0,
            },
            {
                'symbol': 'KQ.m@CZCE.RM', 'datetime': '2024-01-16 09:00:00',
                'open': 3499.0, 'high': 3501.0, 'low': 3498.0, 'close': 3500.0,
                'volume': 1, 'oi': 1, 'month_change': 1,
                'underlying_symbol': 'RM405', 'adjust_factor': 1.0,
            },
        ])

        marked = self.loader._attach_intraday_rollover_marks(frame)
        event = marked.loc[marked['month_change'].eq(1)].iloc[0]

        self.assertEqual(event['datetime'], pd.Timestamp('2024-01-16 09:00:00'))
        self.assertEqual(event['roll_old_close'], 3448.0)
        self.assertEqual(event['roll_new_open'], 3499.0)


class _FakeAccount:
    def __init__(self):
        self.positions = {
            ('al', Direction.LONG): {'volume': 2},
        }

    @staticmethod
    def _get_position_key(symbol, direction):
        return symbol, direction

    @staticmethod
    def _position_volume(position):
        return position['volume']


class _FakeBroker:
    def __init__(self):
        self.account = _FakeAccount()
        self.calls = []

    def execute_rollover(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class RolloverHandlerTests(unittest.TestCase):
    def test_handler_passes_exact_contracts_and_prices(self):
        broker = _FakeBroker()
        handler = MainContractRollover()
        now = datetime(2023, 6, 20, 21, 0)
        bar_data = {
            'al': {
                'is_fresh': True,
                'month_change': 1,
                'previous_underlying_symbol': 'al2307',
                'underlying_symbol': 'al2308',
                'roll_old_close': 18545.0,
                'roll_new_open': 18235.0,
            }
        }

        count = handler.process(broker, now, bar_data, {'al': 99999.0})

        self.assertEqual(count, 1)
        self.assertEqual(len(broker.calls), 1)
        args, kwargs = broker.calls[0]
        self.assertEqual(args[3], 18545.0)
        self.assertEqual(args[4], 18235.0)
        self.assertEqual(kwargs['old_contract'], 'al2307')
        self.assertEqual(kwargs['new_contract'], 'al2308')

    def test_handler_rejects_same_contract_event(self):
        broker = _FakeBroker()
        handler = MainContractRollover()
        bar_data = {
            'al': {
                'is_fresh': True,
                'month_change': 1,
                'previous_underlying_symbol': 'al2308',
                'underlying_symbol': 'al2308',
                'roll_old_close': 18545.0,
                'roll_new_open': 18545.0,
            }
        }

        count = handler.process(broker, datetime(2023, 6, 20, 21, 0), bar_data, {})

        self.assertEqual(count, 0)
        self.assertEqual(broker.calls, [])


class EngineRolloverMetadataTests(unittest.TestCase):
    def test_extract_bar_data_preserves_rollover_metadata(self):
        symbol = 'KQ.m@SHFE.al'
        row = pd.Series({
            ('open', symbol): 18235.0,
            ('high', symbol): 18240.0,
            ('low', symbol): 18230.0,
            ('close', symbol): 18238.0,
            ('volume', symbol): 1.0,
            ('is_fresh', symbol): 1.0,
            ('month_change', symbol): 1.0,
            ('underlying_symbol', symbol): 'al2308',
            ('previous_underlying_symbol', symbol): 'al2307',
            ('roll_old_close', symbol): 18545.0,
            ('roll_new_open', symbol): 18235.0,
        })

        bar = extract_bar_data(row, [symbol])['al']

        self.assertEqual(bar['underlying_symbol'], 'al2308')
        self.assertEqual(bar['previous_underlying_symbol'], 'al2307')
        self.assertEqual(bar['roll_old_close'], 18545.0)
        self.assertEqual(bar['roll_new_open'], 18235.0)


if __name__ == '__main__':
    unittest.main()
