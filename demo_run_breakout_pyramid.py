# -*- coding: utf-8 -*-
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.breakout_pyramid import BreakoutPyramidStrategy


TARGET_SYMBOL = "rb"
FREQ = "1d"
DATA_TYPE = "main"
START_DATE = "2020-01-01 00:00:00"
END_DATE = "2026-05-20 23:59:59"
INITIAL_CAPITAL = 5000000.0

STRATEGY_KWARGS = {
    "lookback": 20,
    "step_volume": 5,
    "max_volume": 20,
    "allow_short": True,
}


if __name__ == "__main__":
    analyzer = run_backtest(
        strategy_class=BreakoutPyramidStrategy,
        symbols_input=TARGET_SYMBOL,
        start_date=START_DATE,
        end_date=END_DATE,
        freq=FREQ,
        data_type=DATA_TYPE,
        initial_capital=INITIAL_CAPITAL,
        strategy_kwargs=STRATEGY_KWARGS,
    )

    build_html_dashboard(analyzer)
