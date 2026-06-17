# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque

import numpy as np
import pandas as pd

from config import pure_product_code
from strategy.general_template import GeneralSignalStrategy


@dataclass
class _SessionState:
    session_key: object | None = None
    cum_tpv: float = 0.0
    cum_volume: float = 0.0
    bars_in_session: int = 0
    deviations: Deque[float] | None = None


@dataclass
class _EntryState:
    direction: int
    price: float
    time: datetime
    bar_index: int


class VWAPBandReversionStrategy(GeneralSignalStrategy):
    """
    Intraday VWAP band mean-reversion strategy for 5-minute bars.

    The strategy resets VWAP by futures trading session, enters against large
    deviations from VWAP, and exits when price reverts toward VWAP or risk
    limits are reached.
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        std_window=48,
        entry_z=2.0,
        exit_z=0.25,
        min_bars_in_session=12,
        min_std_ticks=4.0,
        max_vwap_slope_ticks=6.0,
        slope_window=6,
        take_profit_ticks=28.0,
        stop_loss_ticks=18.0,
        max_hold_bars=24,
        cooldown_bars=3,
        max_entries_per_symbol_per_day=20,
        session_start_hour=21,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.std_window = int(std_window)
        self.entry_z = float(entry_z)
        self.exit_z = float(exit_z)
        self.min_bars_in_session = int(min_bars_in_session)
        self.min_std_ticks = float(min_std_ticks)
        self.max_vwap_slope_ticks = float(max_vwap_slope_ticks)
        self.slope_window = int(slope_window)
        self.take_profit_ticks = float(take_profit_ticks)
        self.stop_loss_ticks = float(stop_loss_ticks)
        self.max_hold_bars = int(max_hold_bars)
        self.cooldown_bars = int(cooldown_bars)
        self.max_entries_per_symbol_per_day = (
            None if max_entries_per_symbol_per_day in (None, "", 0) else int(max_entries_per_symbol_per_day)
        )
        self.session_start_hour = int(session_start_hour)

        if self.std_window < 3:
            raise ValueError("std_window must be at least 3")
        if self.entry_z <= 0:
            raise ValueError("entry_z must be positive")
        if self.exit_z < 0:
            raise ValueError("exit_z cannot be negative")
        if self.min_bars_in_session < 1:
            raise ValueError("min_bars_in_session must be positive")
        if self.slope_window < 1:
            raise ValueError("slope_window must be positive")
        if self.max_hold_bars <= 0:
            raise ValueError("max_hold_bars must be positive")

        self.session_state: dict[str, _SessionState] = {
            sym: _SessionState(deviations=deque(maxlen=self.std_window)) for sym in self.symbols
        }
        self.vwap_history: dict[str, Deque[float]] = {
            sym: deque(maxlen=max(self.slope_window + 1, 2)) for sym in self.symbols
        }
        self.bar_count: dict[str, int] = {sym: 0 for sym in self.symbols}
        self.entry_state: dict[str, _EntryState | None] = {sym: None for sym in self.symbols}
        self.last_exit_bar: dict[str, int | None] = {sym: None for sym in self.symbols}
        self.entry_count_date: dict[str, object | None] = {sym: None for sym in self.symbols}
        self.entry_count: dict[str, int] = {sym: 0 for sym in self.symbols}
        self.last_snapshot: dict[str, dict | None] = {sym: None for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy VWAPBandReversion] symbols={self.symbols} | "
            f"std_window={self.std_window} | entry_z={self.entry_z:g} | exit_z={self.exit_z:g}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}
        for sym in self.symbols:
            raw_bar = bar_data.get(sym)
            if not raw_bar:
                continue

            bar = self._coerce_bar(raw_bar)
            if bar is None:
                continue

            self.bar_count[sym] += 1
            snapshot = self._update_session(sym, bar)
            self.last_snapshot[sym] = snapshot

            price = bar["close"]
            current_net = self.get_net_position(sym)
            self._sync_entry_state(sym, current_net, price)

            if current_net != 0:
                signals[sym] = self._position_signal(sym, price, current_net, snapshot)
            else:
                signals[sym] = self._flat_signal(sym, price, current_net, snapshot)
        return signals

    def _flat_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        blocked = self._entry_block_reason(sym)
        if blocked:
            return self._hold_signal(blocked, price, current_net, snapshot)

        if not snapshot["ready"]:
            return self._hold_signal(snapshot["reason"], price, current_net, snapshot)

        zscore = snapshot["zscore"]
        if zscore <= -self.entry_z:
            self._register_entry(sym)
            return self._open_signal(1, "vwap_lower_band_reversion", self._metrics(price, current_net, snapshot))
        if zscore >= self.entry_z:
            self._register_entry(sym)
            return self._open_signal(-1, "vwap_upper_band_reversion", self._metrics(price, current_net, snapshot))
        return self._hold_signal("hold", price, current_net, snapshot)

    def _position_signal(self, sym: str, price: float, current_net: int, snapshot: dict) -> dict:
        metrics = self._metrics(price, current_net, snapshot)
        state = self.entry_state.get(sym)
        direction = 1 if current_net > 0 else -1
        zscore = snapshot.get("zscore")

        if zscore is not None:
            if direction > 0 and zscore >= -self.exit_z:
                return self._exit_signal("vwap_mean_exit", metrics)
            if direction < 0 and zscore <= self.exit_z:
                return self._exit_signal("vwap_mean_exit", metrics)

        if state is not None:
            tick_size = self._tick_size(sym)
            pnl_ticks = ((price - state.price) / tick_size) * direction
            holding_bars = self.bar_count[sym] - state.bar_index
            metrics["pnl_ticks"] = pnl_ticks
            metrics["holding_bars"] = holding_bars
            if pnl_ticks >= self.take_profit_ticks:
                return self._exit_signal("take_profit", metrics)
            if pnl_ticks <= -self.stop_loss_ticks:
                return self._exit_signal("stop_loss", metrics)
            if holding_bars >= self.max_hold_bars:
                return self._exit_signal("time_exit", metrics)

        return {"signal": None, "reason": "holding", "metrics": metrics}

    def _update_session(self, sym: str, bar: dict) -> dict:
        state = self.session_state[sym]
        session_key = self._session_key(bar["datetime"])
        if state.session_key != session_key:
            state.session_key = session_key
            state.cum_tpv = 0.0
            state.cum_volume = 0.0
            state.bars_in_session = 0
            state.deviations = deque(maxlen=self.std_window)
            self.vwap_history[sym].clear()

        typical_price = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        volume = max(float(bar["volume"]), 1.0)
        state.cum_tpv += typical_price * volume
        state.cum_volume += volume
        state.bars_in_session += 1
        vwap = state.cum_tpv / state.cum_volume

        deviation = bar["close"] - vwap
        assert state.deviations is not None
        state.deviations.append(deviation)
        self.vwap_history[sym].append(vwap)

        std = float(np.std(list(state.deviations), ddof=0)) if len(state.deviations) >= 2 else 0.0
        tick_size = self._tick_size(sym)
        std_ticks = std / tick_size if tick_size > 0 else 0.0
        zscore = deviation / std if std > 0 else 0.0
        slope_ticks = self._vwap_slope_ticks(sym, tick_size)

        ready = True
        reason = "ready"
        if state.bars_in_session < self.min_bars_in_session:
            ready = False
            reason = "session_warming_up"
        elif len(state.deviations) < min(self.std_window, state.bars_in_session):
            ready = False
            reason = "std_warming_up"
        elif std_ticks < self.min_std_ticks:
            ready = False
            reason = "std_too_low"
        elif abs(slope_ticks) > self.max_vwap_slope_ticks:
            ready = False
            reason = "vwap_trending"

        return {
            "ready": ready,
            "reason": reason,
            "session_key": str(session_key),
            "bars_in_session": state.bars_in_session,
            "vwap": vwap,
            "price": bar["close"],
            "deviation": deviation,
            "std": std,
            "std_ticks": std_ticks,
            "zscore": zscore,
            "vwap_slope_ticks": slope_ticks,
        }

    def _vwap_slope_ticks(self, sym: str, tick_size: float) -> float:
        history = self.vwap_history[sym]
        if tick_size <= 0 or len(history) <= self.slope_window:
            return 0.0
        return (history[-1] - history[0]) / tick_size

    def _entry_block_reason(self, sym: str) -> str | None:
        if self._has_pending_order(sym):
            return "pending_order"
        last_exit_bar = self.last_exit_bar.get(sym)
        if last_exit_bar is not None and self.bar_count[sym] - last_exit_bar < self.cooldown_bars:
            return "cooldown"
        if self._entry_limit_reached(sym):
            return "daily_entry_limit"
        return None

    def _sync_entry_state(self, sym: str, current_net: int, price: float):
        state = self.entry_state.get(sym)
        if current_net == 0:
            if state is not None:
                self.last_exit_bar[sym] = self.bar_count[sym]
            self.entry_state[sym] = None
            return

        direction = 1 if current_net > 0 else -1
        if state is not None and state.direction == direction:
            return

        trade = self._last_trade_for_symbol(sym)
        entry_price = float(trade.price) if trade is not None else float(price)
        entry_time = trade.trade_time if trade is not None else self.current_time
        self.entry_state[sym] = _EntryState(direction, entry_price, entry_time, self.bar_count[sym])

    def _last_trade_for_symbol(self, sym: str):
        raw_code = pure_product_code(sym)
        for trade in reversed(self.broker.trade_history):
            if pure_product_code(trade.symbol) == raw_code:
                return trade
        return None

    def _has_pending_order(self, sym: str) -> bool:
        raw_code = pure_product_code(sym)
        return any(pure_product_code(order.symbol) == raw_code for order in self.broker.pending_orders)

    def _entry_limit_reached(self, sym: str) -> bool:
        if self.max_entries_per_symbol_per_day is None:
            return False
        current_date = self.current_time.date()
        if self.entry_count_date.get(sym) != current_date:
            self.entry_count_date[sym] = current_date
            self.entry_count[sym] = 0
        return self.entry_count[sym] >= self.max_entries_per_symbol_per_day

    def _register_entry(self, sym: str):
        if self.max_entries_per_symbol_per_day is None:
            return
        current_date = self.current_time.date()
        if self.entry_count_date.get(sym) != current_date:
            self.entry_count_date[sym] = current_date
            self.entry_count[sym] = 0
        self.entry_count[sym] += 1

    def _coerce_bar(self, bar: dict) -> dict | None:
        close = bar.get("close")
        if close is None or pd.isna(close):
            return None
        close = float(close)
        return {
            "datetime": self.current_time,
            "open": self._float_or_default(bar.get("open"), close),
            "high": self._float_or_default(bar.get("high"), close),
            "low": self._float_or_default(bar.get("low"), close),
            "close": close,
            "volume": self._float_or_default(bar.get("volume"), 0.0),
        }

    @staticmethod
    def _float_or_default(value, default: float) -> float:
        if value is None or pd.isna(value):
            return float(default)
        return float(value)

    def _session_key(self, timestamp: datetime):
        if timestamp.hour >= self.session_start_hour:
            return (timestamp + timedelta(days=1)).date()
        return timestamp.date()

    def _metrics(self, price: float, current_net: int, snapshot: dict) -> dict:
        return {"price": price, "current_net": current_net, **{f"indicator_{k}": v for k, v in snapshot.items()}}

    @staticmethod
    def _open_signal(direction: int, reason: str, metrics: dict) -> dict:
        return {"signal": int(direction), "position_mode": "target", "reason": reason, "metrics": metrics}

    @staticmethod
    def _hold_signal(reason: str, price: float, current_net: int, snapshot: dict) -> dict:
        return {
            "signal": None,
            "reason": reason,
            "metrics": {"price": price, "current_net": current_net, **{f"indicator_{k}": v for k, v in snapshot.items()}},
        }

    @staticmethod
    def _exit_signal(reason: str, metrics: dict) -> dict:
        return {"signal": 0, "position_mode": "flat", "reason": reason, "metrics": metrics}

    def _tick_size(self, sym: str) -> float:
        raw_code = pure_product_code(sym)
        meta = self.account.fee_model._get_meta_data(raw_code)
        return float(meta["tick_size"])
