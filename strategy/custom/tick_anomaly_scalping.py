# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque

import numpy as np
import pandas as pd

from config import pure_product_code
from strategy.general_template import GeneralSignalStrategy


@dataclass
class EntryState:
    direction: int
    price: float
    time: datetime


class TickAnomalyScalpingStrategy(GeneralSignalStrategy):
    """
    Tick-level scalping strategy driven by short-horizon unilateral shocks.

    The strategy estimates an anomaly threshold from past tick moves only.
    Current moves are compared with the historical threshold before they are
    appended to the rolling history, so the signal path does not use future data.
    """

    REVERSAL_MODES = {"reversal", "fade_after_pause"}
    VALID_MODES = {"fade", "follow", "reversal", "fade_after_pause"}

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        scalp_mode="reversal",
        shock_window_seconds=3.0,
        lookback_days=10.0,
        tail_prob=0.001,
        min_move_bps=4.0,
        min_history_samples=500,
        directional_ratio=0.75,
        max_spread_ticks=3.0,
        hold_seconds=8.0,
        take_profit_ticks=2.0,
        stop_loss_ticks=4.0,
        cooldown_seconds=10.0,
        threshold_refresh_ticks=100,
        pause_seconds=1.0,
        reversal_confirm_seconds=1.5,
        reversal_retrace_ratio=0.4,
        reversal_min_retrace_ticks=2.0,
        avoid_session_close_seconds=0.0,
        exit_order_type="opponent",
        exit_order_ttl_seconds=2.0,
        require_history_ready=True,
        warmup_days=10.0,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )

        self.scalp_mode = str(scalp_mode).strip().lower()
        if self.scalp_mode not in self.VALID_MODES:
            raise ValueError(f"scalp_mode must be one of {sorted(self.VALID_MODES)}")

        self.shock_window_seconds = float(shock_window_seconds)
        self.lookback_seconds = float(lookback_days) * 24.0 * 60.0 * 60.0
        self.tail_prob = float(tail_prob)
        self.min_move_bps = float(min_move_bps)
        self.min_history_samples = int(min_history_samples)
        self.directional_ratio = float(directional_ratio)
        self.max_spread_ticks = float(max_spread_ticks)
        self.hold_seconds = float(hold_seconds)
        self.take_profit_ticks = float(take_profit_ticks)
        self.stop_loss_ticks = float(stop_loss_ticks)
        self.cooldown_seconds = float(cooldown_seconds)
        self.threshold_refresh_ticks = max(1, int(threshold_refresh_ticks))
        self.pause_seconds = float(pause_seconds)
        self.reversal_confirm_seconds = float(reversal_confirm_seconds)
        self.reversal_retrace_ratio = float(reversal_retrace_ratio)
        self.reversal_min_retrace_ticks = float(reversal_min_retrace_ticks)
        self.avoid_session_close_seconds = float(avoid_session_close_seconds)
        self.exit_order_type = str(exit_order_type).strip().lower()
        self.exit_order_ttl_seconds = None if exit_order_ttl_seconds is None else float(exit_order_ttl_seconds)
        self.require_history_ready = bool(require_history_ready)
        self.warmup_seconds = float(warmup_days) * 24.0 * 60.0 * 60.0

        if self.shock_window_seconds <= 0:
            raise ValueError("shock_window_seconds must be positive")
        if self.lookback_seconds <= self.shock_window_seconds:
            raise ValueError("lookback_days must cover more time than shock_window_seconds")
        if not 0 < self.tail_prob < 1:
            raise ValueError("tail_prob must be between 0 and 1")
        if not 0 <= self.directional_ratio <= 1:
            raise ValueError("directional_ratio must be between 0 and 1")
        if self.reversal_confirm_seconds <= 0:
            raise ValueError("reversal_confirm_seconds must be positive")
        if not 0 <= self.reversal_retrace_ratio <= 1:
            raise ValueError("reversal_retrace_ratio must be between 0 and 1")
        if self.avoid_session_close_seconds < 0:
            raise ValueError("avoid_session_close_seconds cannot be negative")
        if self.exit_order_type not in {"market", "opponent", "limit"}:
            raise ValueError("exit_order_type must be market, opponent, or limit")
        if self.exit_order_ttl_seconds is not None and self.exit_order_ttl_seconds < 0:
            raise ValueError("exit_order_ttl_seconds cannot be negative")
        if self.warmup_seconds < 0:
            raise ValueError("warmup_days cannot be negative")

        self.price_windows: dict[str, Deque[tuple[datetime, float]]] = {sym: deque() for sym in self.symbols}
        self.move_history: dict[str, Deque[tuple[datetime, float]]] = {sym: deque() for sym in self.symbols}
        self.entry_state: dict[str, EntryState | None] = {sym: None for sym in self.symbols}
        self.last_exit_time: dict[str, datetime | None] = {sym: None for sym in self.symbols}
        self.pending_shock: dict[str, dict | None] = {sym: None for sym in self.symbols}
        self.threshold_cache: dict[str, float] = {sym: self.min_move_bps for sym in self.symbols}
        self.threshold_update_counter: dict[str, int] = {sym: 0 for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy TickScalp] symbols={self.symbols} | mode={self.scalp_mode} | "
            f"shock_window={self.shock_window_seconds:g}s | tail_prob={self.tail_prob:g} | "
            f"hold={self.hold_seconds:g}s"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}
        current_time = self.current_time

        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not bar:
                continue

            price = self._select_price(bar)
            if price is None:
                continue

            is_fresh = bool(bar.get("is_fresh", True))
            current_net = self.get_net_position(sym)
            self._sync_entry_state(sym, current_net, price)

            if not is_fresh:
                signals[sym] = self._hold_signal("stale_tick", price, current_net)
                continue

            window = self.price_windows.setdefault(sym, deque())
            self._append_price(window, current_time, price)
            self._trim_price_window(window, current_time)

            if current_net != 0:
                signals[sym] = self._exit_signal(sym, bar, price, current_net)
                continue

            if self._has_pending_order(sym):
                signals[sym] = self._hold_signal("pending_order", price, current_net)
                self._record_current_move(sym, current_time, window)
                continue

            if self._in_cooldown(sym, current_time):
                signals[sym] = self._hold_signal("cooldown", price, current_net)
                self._record_current_move(sym, current_time, window)
                continue

            if not self._spread_ok(sym, bar):
                signals[sym] = self._hold_signal("spread_too_wide", price, current_net)
                self._record_current_move(sym, current_time, window)
                continue

            if self._near_session_close(current_time):
                signals[sym] = self._hold_signal("near_session_close", price, current_net)
                self._record_current_move(sym, current_time, window)
                continue

            signal = self._entry_signal(sym, current_time, price, window)
            signals[sym] = signal if signal is not None else self._hold_signal("hold", price, current_net)
            self._record_current_move(sym, current_time, window)

        return signals

    def _select_price(self, bar: dict) -> float | None:
        for field in ("mid_price", "last_price", "close"):
            value = bar.get(field)
            if value is not None and not pd.isna(value) and float(value) > 0:
                return float(value)
        return None

    def _append_price(self, window: Deque[tuple[datetime, float]], current_time: datetime, price: float):
        if not window or current_time >= window[-1][0]:
            window.append((current_time, price))
            return
        window.clear()
        window.append((current_time, price))

    def _trim_price_window(self, window: Deque[tuple[datetime, float]], current_time: datetime):
        max_age = self.shock_window_seconds + self.pause_seconds + 1.0
        while window and self._seconds_between(current_time, window[0][0]) > max_age:
            window.popleft()

    def _record_current_move(self, sym: str, current_time: datetime, window: Deque[tuple[datetime, float]]):
        shock = self._shock_from_window(window, current_time)
        if shock is None:
            return
        _, magnitude_bps, _, _ = shock
        history = self.move_history.setdefault(sym, deque())
        history.append((current_time, magnitude_bps))
        while history and self._seconds_between(current_time, history[0][0]) > self.lookback_seconds:
            history.popleft()
        self.threshold_update_counter[sym] = self.threshold_update_counter.get(sym, 0) + 1

    def _entry_signal(
        self,
        sym: str,
        current_time: datetime,
        price: float,
        window: Deque[tuple[datetime, float]],
    ) -> dict | None:
        if self.scalp_mode in self.REVERSAL_MODES:
            reversal_signal = self._confirmed_reversal_entry(sym, current_time, price)
            if reversal_signal is not None:
                return reversal_signal
            if self.pending_shock.get(sym):
                return self._hold_signal("pending_reversal", price, 0)

        shock = self._shock_from_window(window, current_time)
        if shock is None:
            return None

        move_bps, magnitude_bps, direction, start_price = shock
        if not self._history_ready(sym, current_time):
            return self._hold_signal(
                "warming_up_threshold",
                price,
                0,
                history_samples=len(self.move_history.get(sym, [])),
                warmup_days=self.warmup_seconds / 86400.0,
            )

        threshold = self._current_threshold(sym)
        if magnitude_bps < threshold:
            return None

        ratio = self._directional_ratio(window, direction, current_time)
        if ratio < self.directional_ratio:
            return None

        if self.scalp_mode in self.REVERSAL_MODES:
            self.pending_shock[sym] = {
                "time": current_time,
                "price": price,
                "extreme_price": price,
                "start_price": start_price,
                "shock_abs_move": abs(price - start_price),
                "direction": direction,
                "threshold_bps": threshold,
                "move_bps": move_bps,
            }
            return self._hold_signal("pending_reversal", price, 0, move_bps=move_bps, threshold_bps=threshold)

        entry_direction = self._entry_direction(direction)
        return self._open_signal(
            entry_direction,
            "shock_entry",
            price=price,
            move_bps=move_bps,
            threshold_bps=threshold,
            directional_ratio=ratio,
        )

    def _confirmed_reversal_entry(self, sym: str, current_time: datetime, price: float) -> dict | None:
        pending = self.pending_shock.get(sym)
        if not pending:
            return None

        elapsed = self._seconds_between(current_time, pending["time"])
        if elapsed > self.reversal_confirm_seconds:
            self.pending_shock[sym] = None
            return None

        shock_direction = int(pending["direction"])
        extreme_price = float(pending["extreme_price"])
        if shock_direction > 0 and price > extreme_price:
            pending["extreme_price"] = price
            pending["shock_abs_move"] = abs(price - float(pending["start_price"]))
            return None
        if shock_direction < 0 and price < extreme_price:
            pending["extreme_price"] = price
            pending["shock_abs_move"] = abs(price - float(pending["start_price"]))
            return None

        tick_size = self._tick_size(sym)
        shock_abs_move = max(float(pending["shock_abs_move"]), tick_size)
        required_retrace = max(
            shock_abs_move * self.reversal_retrace_ratio,
            self.reversal_min_retrace_ticks * tick_size,
        )
        actual_retrace = (
            float(pending["extreme_price"]) - price
            if shock_direction > 0
            else price - float(pending["extreme_price"])
        )
        if actual_retrace < required_retrace:
            return None

        self.pending_shock[sym] = None
        entry_direction = -shock_direction
        return self._open_signal(
            entry_direction,
            "confirmed_reversal_entry",
            price=price,
            move_bps=float(pending["move_bps"]),
            threshold_bps=float(pending["threshold_bps"]),
            actual_retrace=actual_retrace,
            required_retrace=required_retrace,
        )

    def _entry_direction(self, shock_direction: int) -> int:
        if self.scalp_mode == "follow":
            return int(shock_direction)
        return -int(shock_direction)

    def _exit_signal(self, sym: str, bar: dict, price: float, current_net: int) -> dict:
        state = self.entry_state.get(sym)
        if state is None:
            return self._hold_signal("position_state_pending", price, current_net)

        tick_size = self._tick_size(sym)
        elapsed = self._seconds_between(self.current_time, state.time)
        pnl_ticks = ((price - state.price) / tick_size) * state.direction

        metrics = {
            "price": price,
            "entry_price": state.price,
            "holding_seconds": elapsed,
            "pnl_ticks": pnl_ticks,
            "current_net": current_net,
        }

        if pnl_ticks >= self.take_profit_ticks:
            return self._exit_order_signal("take_profit", metrics)
        if pnl_ticks <= -self.stop_loss_ticks:
            return self._exit_order_signal("stop_loss", metrics)
        if elapsed >= self.hold_seconds:
            return self._exit_order_signal("time_exit", metrics)

        return {"signal": None, "reason": "holding", "metrics": metrics}

    def _shock_from_window(
        self,
        window: Deque[tuple[datetime, float]],
        current_time: datetime,
    ) -> tuple[float, float, int, float] | None:
        if len(window) < 2:
            return None

        start_time = None
        start_price = None
        for candidate_time, candidate_price in window:
            if self._seconds_between(current_time, candidate_time) <= self.shock_window_seconds:
                start_time = candidate_time
                start_price = candidate_price
                break

        if start_time is None or start_price is None:
            return None

        elapsed = self._seconds_between(current_time, start_time)
        if elapsed < max(0.5, self.shock_window_seconds * 0.5):
            return None
        if start_price <= 0:
            return None

        current_price = window[-1][1]
        move_bps = (current_price / start_price - 1.0) * 10000.0
        direction = 1 if move_bps > 0 else -1 if move_bps < 0 else 0
        if direction == 0:
            return None
        return move_bps, abs(move_bps), direction, float(start_price)

    def _directional_ratio(
        self,
        window: Deque[tuple[datetime, float]],
        direction: int,
        current_time: datetime,
    ) -> float:
        total = 0
        same_direction = 0
        previous = None
        for tick_time, price in window:
            if self._seconds_between(current_time, tick_time) > self.shock_window_seconds:
                continue
            if previous is None:
                previous = price
                continue
            change = price - previous
            previous = price
            if change == 0:
                continue
            total += 1
            if change * direction > 0:
                same_direction += 1
        if total == 0:
            return 0.0
        return same_direction / total

    def _current_threshold(self, sym: str) -> float:
        history = self.move_history.setdefault(sym, deque())
        if len(history) < self.min_history_samples:
            return self.min_move_bps

        counter = self.threshold_update_counter.get(sym, 0)
        if counter % self.threshold_refresh_ticks != 0:
            return self.threshold_cache.get(sym, self.min_move_bps)

        values = np.array([value for _, value in history], dtype=float)
        threshold = float(np.quantile(values, 1.0 - self.tail_prob))
        threshold = max(self.min_move_bps, threshold)
        self.threshold_cache[sym] = threshold
        return threshold

    def _history_ready(self, sym: str, current_time: datetime) -> bool:
        if not self.require_history_ready:
            return True

        history = self.move_history.setdefault(sym, deque())
        if len(history) < self.min_history_samples:
            return False
        if self.warmup_seconds <= 0:
            return True
        return self._seconds_between(current_time, history[0][0]) >= min(self.warmup_seconds, self.lookback_seconds)

    def _spread_ok(self, sym: str, bar: dict) -> bool:
        spread = bar.get("spread")
        if spread is None or pd.isna(spread):
            return True
        if float(spread) <= 0:
            return True
        return float(spread) <= self.max_spread_ticks * self._tick_size(sym)

    def _tick_size(self, sym: str) -> float:
        raw_code = pure_product_code(sym)
        meta = self.account.fee_model._get_meta_data(raw_code)
        return float(meta["tick_size"])

    def _sync_entry_state(self, sym: str, current_net: int, current_price: float):
        state = self.entry_state.get(sym)
        if current_net == 0:
            if state is not None:
                self.last_exit_time[sym] = self.current_time
            self.entry_state[sym] = None
            return

        direction = 1 if current_net > 0 else -1
        if state is not None and state.direction == direction:
            return

        trade = self._last_trade_for_symbol(sym)
        entry_price = float(trade.price) if trade is not None else float(current_price)
        entry_time = trade.trade_time if trade is not None else self.current_time
        self.entry_state[sym] = EntryState(direction=direction, price=entry_price, time=entry_time)

    def _last_trade_for_symbol(self, sym: str):
        raw_code = pure_product_code(sym)
        for trade in reversed(self.broker.trade_history):
            if pure_product_code(trade.symbol) == raw_code:
                return trade
        return None

    def _in_cooldown(self, sym: str, current_time: datetime) -> bool:
        last_exit = self.last_exit_time.get(sym)
        if last_exit is None:
            return False
        return self._seconds_between(current_time, last_exit) < self.cooldown_seconds

    def _has_pending_order(self, sym: str) -> bool:
        raw_code = pure_product_code(sym)
        return any(pure_product_code(order.symbol) == raw_code for order in self.broker.pending_orders)

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

    def _exit_order_signal(self, reason: str, metrics: dict) -> dict:
        return {
            "signal": 0,
            "position_mode": "flat",
            "reason": reason,
            "metrics": metrics,
            "order_type": self.exit_order_type,
            "order_ttl_seconds": self.exit_order_ttl_seconds,
        }

    @staticmethod
    def _open_signal(direction: int, reason: str, **metrics) -> dict:
        return {
            "signal": int(direction),
            "position_mode": "target",
            "reason": reason,
            "metrics": metrics,
        }
