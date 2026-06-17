# run_scripts/run_tick_anomaly_scalping_gold.py
# -*- coding: utf-8 -*-

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest_engine import run_backtest
from frontend_index import build_html_dashboard
from strategy.custom.tick_anomaly_scalping import TickAnomalyScalpingStrategy


TARGET_SYMBOLS = ["au", "ag"]
START_DATE = "2026-05-01 09:00:00"
END_DATE = "2026-05-15 15:00:00"

STRATEGY_KWARGS = {
    "target_symbols": TARGET_SYMBOLS,
    # Confirmed reversal with session-close filtering.
    "scalp_mode": "reversal",
    "shock_window_seconds": 5.0,
    "lookback_days": 1.5,
    "tail_prob": 0.02,
    "min_move_bps": 1.5,
    "min_history_samples": 1500,
    "directional_ratio": 0.55,
    "max_spread_ticks": 2.0,
    "hold_seconds": 5.0,
    "take_profit_ticks": 8.0,
    "stop_loss_ticks": 8.0,
    "cooldown_seconds": 20.0,
    "threshold_refresh_ticks": 50,
    "pause_seconds": 1.0,
    "reversal_confirm_seconds": 1.0,
    "reversal_retrace_ratio": 0.50,
    "reversal_min_retrace_ticks": 2.0,
    "avoid_session_close_seconds": 90.0,
    "exit_order_type": "opponent",
    "exit_order_ttl_seconds": 2.0,
    "require_history_ready": True,
    "warmup_days": 0.25,
    "sizing": {
        "mode": "fixed_volume",
        "value": 1,
        "min_volume": 1,
        "max_volume": 1,
        "round_lot": 1,
    },
    "execution": {
        "order_type": "limit",
        "price_field": "mid_price",
        "slippage_ticks": 0.0,
        "limit_mode": "at_close",
        "ticks": 0.0,
        "order_ttl_seconds": 2.0,
    },
    "exit": {
        "close_pct": 1.0,
        "allow_reverse": False,
        "respect_pending_orders": True,
    },
    "record_signals": True,
}


if __name__ == "__main__":
    analyzer = run_backtest(
        strategy_class=TickAnomalyScalpingStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date=START_DATE,
        end_date=END_DATE,
        freq="tick",
        data_type="main",
        initial_capital=1_000_000.0,
        strategy_kwargs=STRATEGY_KWARGS,
        enable_main_rollover=False,
    )

    if analyzer is not None:
        build_html_dashboard(analyzer)
