# -*- coding: utf-8 -*-
import unittest
from datetime import datetime
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import (
    _resolve_trading_date,
    extract_bar_data,
    extract_tick_bar_data_from_tuple,
)
from broker.match_engine import MatchEngine
from broker.order import Direction, Offset, Trade
from broker.rollover import MainContractRollover
from data_feed.ch_loader import ClickHouseLoader
from portfolio.account import Account


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

    def test_authoritative_trade_date_keeps_friday_night_in_monday_session(self):
        frame = pd.DataFrame([
            {
                'symbol': 'KQ.m@SHFE.rb', 'datetime': '2026-05-15 14:59:00',
                'open': 3100.0, 'high': 3102.0, 'low': 3099.0, 'close': 3101.0,
                'volume': 1, 'oi': 1, 'month_change': 0,
                'underlying_symbol': 'rb2610', 'previous_underlying_symbol': 'rb2610',
                'mapping_trade_date': '2026-05-15', 'adjust_factor': 1.0,
            },
            {
                'symbol': 'KQ.m@SHFE.rb', 'datetime': '2026-05-15 21:00:00',
                'open': 3110.0, 'high': 3112.0, 'low': 3108.0, 'close': 3111.0,
                'volume': 1, 'oi': 1, 'month_change': 0,
                'underlying_symbol': 'rb2610', 'previous_underlying_symbol': 'rb2610',
                'mapping_trade_date': '2026-05-18', 'adjust_factor': 1.0,
            },
            {
                'symbol': 'KQ.m@SHFE.rb', 'datetime': '2026-05-18 09:00:00',
                'open': 3120.0, 'high': 3122.0, 'low': 3118.0, 'close': 3121.0,
                'volume': 1, 'oi': 1, 'month_change': 0,
                'underlying_symbol': 'rb2610', 'previous_underlying_symbol': 'rb2610',
                'mapping_trade_date': '2026-05-18', 'adjust_factor': 1.0,
            },
        ])

        marked = self.loader._attach_intraday_rollover_marks(frame)

        self.assertEqual(len(marked), 3)
        self.assertEqual(marked['month_change'].sum(), 0)
        self.assertNotIn('mapping_trade_date', marked.columns)

    def test_tick_rollover_uses_last_ticks_as_old_close_and_new_open(self):
        frame = pd.DataFrame([
            {
                'symbol': 'KQ.m@SHFE.au', 'datetime': '2026-04-01 14:59:59.500',
                'last_price': 1020.0, 'volume': 10, 'month_change': 0,
                'underlying_symbol': 'au2606', 'previous_underlying_symbol': 'au2606',
                'mapping_trade_date': '2026-04-01',
            },
            {
                'symbol': 'KQ.m@SHFE.au', 'datetime': '2026-04-01 21:00:00.500',
                'last_price': 1028.0, 'volume': 1, 'month_change': 1,
                'underlying_symbol': 'au2608', 'previous_underlying_symbol': 'au2606',
                'mapping_trade_date': '2026-04-02',
            },
        ])

        marked = self.loader._attach_intraday_rollover_marks(frame)
        event = marked.loc[marked['month_change'].eq(1)].iloc[0]

        self.assertEqual(event['previous_underlying_symbol'], 'au2606')
        self.assertEqual(event['underlying_symbol'], 'au2608')
        self.assertEqual(event['roll_old_close'], 1020.0)
        self.assertEqual(event['roll_new_open'], 1028.0)


class DailyRolloverMarkTests(unittest.TestCase):
    def setUp(self):
        self.loader = object.__new__(ClickHouseLoader)

    def test_daily_rollover_metadata_is_complete(self):
        frame = pd.DataFrame([
            {
                'symbol': 'KQ.m@SHFE.rb', 'datetime': '2026-04-01',
                'open': 3130.0, 'high': 3135.0, 'low': 3118.0, 'close': 3120.0,
                'volume': 1, 'oi': 1, 'month_change': 0,
                'underlying_symbol': 'rb2605', 'adjust_factor': 1.0,
            },
            {
                'symbol': 'KQ.m@SHFE.rb', 'datetime': '2026-04-02',
                'open': 3126.0, 'high': 3130.0, 'low': 3100.0, 'close': 3106.0,
                'volume': 1, 'oi': 1, 'month_change': 1,
                'underlying_symbol': 'rb2610', 'adjust_factor': 1.0,
            },
        ])

        marked = self.loader._attach_daily_rollover_marks(frame)
        event = marked.loc[marked['month_change'].eq(1)].iloc[0]

        self.assertEqual(event['previous_underlying_symbol'], 'rb2605')
        self.assertEqual(event['underlying_symbol'], 'rb2610')
        self.assertEqual(event['roll_old_close'], 3120.0)
        self.assertEqual(event['roll_new_open'], 3126.0)


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


class RolloverExecutionTests(unittest.TestCase):
    def test_real_account_keeps_volume_and_rebases_to_new_contract(self):
        account = Account(initial_capital=1_000_000.0)
        broker = MatchEngine(account)
        broker.fee_model.default_slippage_ticks = 0.0
        account.fee_model.default_slippage_ticks = 0.0
        account.process_trade(Trade(
            symbol='rb', direction=Direction.LONG, offset=Offset.OPEN,
            volume=2, price=3100.0, trade_time=datetime(2026, 4, 1, 9, 0),
            commission=0.0, slippage_cost=0.0, order_id='seed_open',
        ))
        account.settle_daily({'rb': 3120.0})
        handler = MainContractRollover()
        bar_data = {
            'rb': {
                'is_fresh': True,
                'month_change': 1,
                'previous_underlying_symbol': 'rb2605',
                'underlying_symbol': 'rb2610',
                'roll_old_close': 3120.0,
                'roll_new_open': 3152.0,
            }
        }

        count = handler.process(broker, datetime(2026, 4, 1, 21, 0), bar_data, {'rb': 3120.0})

        position = account.positions[account._get_position_key('rb', Direction.LONG)]
        rollover_trades = [trade for trade in broker.trade_history if trade.is_rollover]
        self.assertEqual(count, 1)
        self.assertEqual(account._position_volume(position), 2)
        self.assertEqual(position['td_avg_price'], 3152.0)
        self.assertEqual(
            [(trade.offset, trade.contract_symbol) for trade in rollover_trades],
            [(Offset.CLOSE, 'rb2605'), (Offset.OPEN, 'rb2610')],
        )


class TradingDateTests(unittest.TestCase):
    def test_night_session_does_not_settle_again_at_calendar_midnight(self):
        monday_trade_date = pd.Timestamp('2026-05-18')
        friday_night = {
            'rb': {'is_fresh': True, 'trading_date': monday_trade_date, 'close': 3100.0}
        }
        saturday_after_midnight = {
            'rb': {'is_fresh': True, 'trading_date': monday_trade_date, 'close': 3101.0}
        }

        first = _resolve_trading_date(datetime(2026, 5, 15, 21, 0), friday_night)
        second = _resolve_trading_date(datetime(2026, 5, 16, 0, 30), saturday_after_midnight)

        self.assertEqual(first, monday_trade_date.date())
        self.assertEqual(second, monday_trade_date.date())


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

    def test_tick_extractor_preserves_rollover_metadata(self):
        symbol = 'KQ.m@SHFE.au'
        columns = [
            ('last_price', symbol),
            ('volume', symbol),
            ('is_fresh', symbol),
            ('month_change', symbol),
            ('underlying_symbol', symbol),
            ('previous_underlying_symbol', symbol),
            ('roll_old_close', symbol),
            ('roll_new_open', symbol),
        ]
        row_values = [1028.0, 1.0, 1.0, 1.0, 'au2608', 'au2606', 1020.0, 1028.0]
        col_pos = {column: idx for idx, column in enumerate(columns)}

        bar = extract_tick_bar_data_from_tuple(row_values, [symbol], col_pos)['au']

        self.assertEqual(bar['month_change'], 1.0)
        self.assertEqual(bar['previous_underlying_symbol'], 'au2606')
        self.assertEqual(bar['underlying_symbol'], 'au2608')
        self.assertEqual(bar['roll_old_close'], 1020.0)
        self.assertEqual(bar['roll_new_open'], 1028.0)


if __name__ == '__main__':
    unittest.main()
