# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from config import pure_product_code
from strategy.general_template import GeneralSignalStrategy


@dataclass
class TickEntryState:
    direction: int
    price: float
    time: datetime


class TickScalpingBase(GeneralSignalStrategy):
    """Shared risk controls for tick-level signal strategies."""

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        max_spread_ticks=2.0,
        hold_seconds=20.0,
        take_profit_ticks=8.0,
        stop_loss_ticks=8.0,
        cooldown_seconds=20.0,
        avoid_session_close_seconds=120.0,
        enabled_entry_symbols=None,
        max_entries_per_symbol_per_day=None,
        allowed_entry_hours=None,
        exit_order_type="opponent",
        exit_order_ttl_seconds=2.0,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.max_spread_ticks = float(max_spread_ticks)
        self.hold_seconds = float(hold_seconds)
        self.take_profit_ticks = float(take_profit_ticks)
        self.stop_loss_ticks = float(stop_loss_ticks)
        self.cooldown_seconds = float(cooldown_seconds)
        self.avoid_session_close_seconds = float(avoid_session_close_seconds)
        self.enabled_entry_symbols = (
            None if enabled_entry_symbols is None else {str(sym).strip().lower() for sym in enabled_entry_symbols}
        )
        self.max_entries_per_symbol_per_day = (
            None if max_entries_per_symbol_per_day is None else int(max_entries_per_symbol_per_day)
        )
        self.allowed_entry_hours = None if allowed_entry_hours is None else {int(hour) for hour in allowed_entry_hours}
        self.exit_order_type = str(exit_order_type).strip().lower()
        self.exit_order_ttl_seconds = None if exit_order_ttl_seconds is None else float(exit_order_ttl_seconds)

        if self.max_spread_ticks < 0:
            raise ValueError("max_spread_ticks cannot be negative")
        if self.hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        if self.take_profit_ticks <= 0:
            raise ValueError("take_profit_ticks must be positive")
        if self.stop_loss_ticks <= 0:
            raise ValueError("stop_loss_ticks must be positive")
        if self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        if self.avoid_session_close_seconds < 0:
            raise ValueError("avoid_session_close_seconds cannot be negative")
        if self.max_entries_per_symbol_per_day is not None and self.max_entries_per_symbol_per_day <= 0:
            raise ValueError("max_entries_per_symbol_per_day must be positive")
        if self.exit_order_type not in {"market", "opponent", "limit"}:
            raise ValueError("exit_order_type must be market, opponent, or limit")

        self.entry_state: dict[str, TickEntryState | None] = {sym: None for sym in self.symbols}
        self.last_exit_time: dict[str, datetime | None] = {sym: None for sym in self.symbols}
        self.entry_count_date: dict[str, object | None] = {sym: None for sym in self.symbols}
        self.entry_count: dict[str, int] = {sym: 0 for sym in self.symbols}

    def _select_price(self, bar: dict) -> float | None:
        for field in ("mid_price", "last_price", "close"):
            value = bar.get(field)
            if value is not None and not pd.isna(value) and float(value) > 0:
                return float(value)
        return None

    def _tick_size(self, symbol: str) -> float:
        raw_code = pure_product_code(symbol)
        meta = self.account.fee_model._get_meta_data(raw_code)
        return float(meta["tick_size"])

    def _spread_ok(self, symbol: str, bar: dict) -> bool:
        spread = bar.get("spread")
        if spread is None or pd.isna(spread):
            return True
        if float(spread) <= 0:
            return True
        return float(spread) <= self.max_spread_ticks * self._tick_size(symbol)

    def _entry_block_signal(self, symbol: str, bar: dict, price: float, current_net: int) -> dict | None:
        if not bool(bar.get("is_fresh", True)):
            return self._hold_signal("stale_tick", price, current_net)
        if self.enabled_entry_symbols is not None and symbol.lower() not in self.enabled_entry_symbols:
            return self._hold_signal("entry_symbol_disabled", price, current_net)
        if self._has_pending_order(symbol):
            return self._hold_signal("pending_order", price, current_net)
        if self._in_cooldown(symbol, self.current_time):
            return self._hold_signal("cooldown", price, current_net)
        if not self._spread_ok(symbol, bar):
            return self._hold_signal("spread_too_wide", price, current_net)
        if self._near_session_close(self.current_time):
            return self._hold_signal("near_session_close", price, current_net)
        if not self._entry_hour_allowed(self.current_time):
            return self._hold_signal("outside_entry_hours", price, current_net)
        if self._entry_limit_reached(symbol, self.current_time):
            return self._hold_signal("daily_entry_limit", price, current_net)
        return None

    def _sync_entry_state(self, symbol: str, current_net: int, current_price: float):
        state = self.entry_state.get(symbol)
        if current_net == 0:
            if state is not None:
                self.last_exit_time[symbol] = self.current_time
            self.entry_state[symbol] = None
            return

        direction = 1 if current_net > 0 else -1
        if state is not None and state.direction == direction:
            return

        trade = self._last_trade_for_symbol(symbol)
        entry_price = float(trade.price) if trade is not None else float(current_price)
        entry_time = trade.trade_time if trade is not None else self.current_time
        self.entry_state[symbol] = TickEntryState(direction=direction, price=entry_price, time=entry_time)

    def _position_metrics(self, symbol: str, price: float, current_net: int, **extra) -> dict:
        state = self.entry_state.get(symbol)
        tick_size = self._tick_size(symbol)
        if state is None:
            metrics = {"price": price, "current_net": current_net}
            metrics.update(extra)
            return metrics

        elapsed = self._seconds_between(self.current_time, state.time)
        pnl_ticks = ((price - state.price) / tick_size) * state.direction
        metrics = {
            "price": price,
            "entry_price": state.price,
            "holding_seconds": elapsed,
            "pnl_ticks": pnl_ticks,
            "current_net": current_net,
        }
        metrics.update(extra)
        return metrics

    def _risk_exit_signal(self, symbol: str, bar: dict, price: float, current_net: int, **extra) -> dict | None:
        if not bool(bar.get("is_fresh", True)):
            return None

        metrics = self._position_metrics(symbol, price, current_net, **extra)
        state = self.entry_state.get(symbol)
        if state is None:
            return None

        if self._near_session_close(self.current_time):
            return self._exit_order_signal("session_close_exit", metrics)
        if metrics["pnl_ticks"] >= self.take_profit_ticks:
            return self._exit_order_signal("take_profit", metrics)
        if metrics["pnl_ticks"] <= -self.stop_loss_ticks:
            return self._exit_order_signal("stop_loss", metrics)
        if metrics["holding_seconds"] >= self.hold_seconds:
            return self._exit_order_signal("time_exit", metrics)
        return None

    def _last_trade_for_symbol(self, symbol: str):
        raw_code = pure_product_code(symbol)
        for trade in reversed(self.broker.trade_history):
            if pure_product_code(trade.symbol) == raw_code:
                return trade
        return None

    def _has_pending_order(self, symbol: str) -> bool:
        raw_code = pure_product_code(symbol)
        return any(pure_product_code(order.symbol) == raw_code for order in self.broker.pending_orders)

    def _in_cooldown(self, symbol: str, current_time: datetime) -> bool:
        last_exit = self.last_exit_time.get(symbol)
        if last_exit is None:
            return False
        return self._seconds_between(current_time, last_exit) < self.cooldown_seconds

    def _near_session_close(self, current_time: datetime) -> bool:
        if self.avoid_session_close_seconds <= 0:
            return False

        seconds_now = current_time.hour * 3600 + current_time.minute * 60 + current_time.second
        close_points = (
            2 * 3600 + 30 * 60,
            10 * 3600 + 15 * 60,
            11 * 3600 + 30 * 60,
            15 * 3600,
            23 * 3600,
        )
        for close_seconds in close_points:
            seconds_to_close = close_seconds - seconds_now
            if 0 <= seconds_to_close <= self.avoid_session_close_seconds:
                return True
        return False

    @staticmethod
    def _seconds_between(later: datetime, earlier: datetime) -> float:
        return (later - earlier).total_seconds()

    @staticmethod
    def _hold_signal(reason: str, price: float, current_net: int, **metrics) -> dict:
        payload = {"price": price, "current_net": current_net}
        payload.update(metrics)
        return {"signal": None, "reason": reason, "metrics": payload}

    @staticmethod
    def _open_signal(direction: int, reason: str, **metrics) -> dict:
        return {
            "signal": int(direction),
            "position_mode": "target",
            "reason": reason,
            "metrics": metrics,
        }

    def _open_symbol_signal(self, symbol: str, direction: int, reason: str, **metrics) -> dict:
        self._register_entry(symbol, self.current_time)
        return self._open_signal(direction, reason, **metrics)

    def _exit_order_signal(self, reason: str, metrics: dict) -> dict:
        return {
            "signal": 0,
            "position_mode": "flat",
            "reason": reason,
            "metrics": metrics,
            "order_type": self.exit_order_type,
            "order_ttl_seconds": self.exit_order_ttl_seconds,
        }

    def _entry_hour_allowed(self, current_time: datetime) -> bool:
        if self.allowed_entry_hours is None:
            return True
        return current_time.hour in self.allowed_entry_hours

    def _entry_limit_reached(self, symbol: str, current_time: datetime) -> bool:
        if self.max_entries_per_symbol_per_day is None:
            return False
        self._reset_entry_count_if_needed(symbol, current_time)
        return self.entry_count.get(symbol, 0) >= self.max_entries_per_symbol_per_day

    def _register_entry(self, symbol: str, current_time: datetime):
        if self.max_entries_per_symbol_per_day is None:
            return
        self._reset_entry_count_if_needed(symbol, current_time)
        self.entry_count[symbol] = self.entry_count.get(symbol, 0) + 1

    def _reset_entry_count_if_needed(self, symbol: str, current_time: datetime):
        current_date = current_time.date()
        if self.entry_count_date.get(symbol) != current_date:
            self.entry_count_date[symbol] = current_date
            self.entry_count[symbol] = 0
