# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Deque

import pandas as pd

from strategy.custom.tick_common import TickScalpingBase


class TickRollingBreakoutStrategy(TickScalpingBase):
    """
    Tick-level rolling breakout continuation strategy.

    A signal is generated only after the current tick breaks the previous
    rolling high/low. The rolling range is computed from ticks that existed
    before the current tick, so the strategy does not use future information.
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        breakout_window_seconds=45.0,
        breakout_mode="follow",
        confirm_window_seconds=6.0,
        min_range_ticks=4.0,
        breakout_ticks=0.0,
        min_directional_ratio=0.55,
        min_ticks_in_window=20,
        min_tick_volume=0.0,
        use_imbalance_filter=False,
        imbalance_threshold=0.15,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.breakout_window_seconds = float(breakout_window_seconds)
        self.breakout_mode = str(breakout_mode).strip().lower()
        self.confirm_window_seconds = float(confirm_window_seconds)
        self.min_range_ticks = float(min_range_ticks)
        self.breakout_ticks = float(breakout_ticks)
        self.min_directional_ratio = float(min_directional_ratio)
        self.min_ticks_in_window = int(min_ticks_in_window)
        self.min_tick_volume = float(min_tick_volume)
        self.use_imbalance_filter = bool(use_imbalance_filter)
        self.imbalance_threshold = float(imbalance_threshold)

        if self.breakout_window_seconds <= 0:
            raise ValueError("breakout_window_seconds must be positive")
        if self.breakout_mode not in {"follow", "fade"}:
            raise ValueError("breakout_mode must be follow or fade")
        if self.confirm_window_seconds <= 0:
            raise ValueError("confirm_window_seconds must be positive")
        if not 0 <= self.min_directional_ratio <= 1:
            raise ValueError("min_directional_ratio must be between 0 and 1")

        self.price_windows: dict[str, Deque[tuple[datetime, float]]] = {sym: deque() for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy TickBreakout] symbols={self.symbols} | window={self.breakout_window_seconds:g}s | "
            f"mode={self.breakout_mode} | confirm={self.confirm_window_seconds:g}s | "
            f"range={self.min_range_ticks:g} ticks"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}
        for sym in self.symbols:
            bar = bar_data.get(sym)
            if not bar:
                continue

            price = self._select_price(bar)
            if price is None:
                continue

            window = self.price_windows.setdefault(sym, deque())
            self._trim_window(window, self.current_time)

            current_net = self.get_net_position(sym)
            self._sync_entry_state(sym, current_net, price)

            signal = None
            if current_net != 0:
                signal = self._risk_exit_signal(sym, bar, price, current_net)
                if signal is None:
                    metrics = self._position_metrics(sym, price, current_net)
                    signal = {"signal": None, "reason": "holding", "metrics": metrics}
            else:
                signal = self._entry_block_signal(sym, bar, price, current_net)
                if signal is None:
                    signal = self._entry_signal(sym, bar, price, window)
                if signal is None:
                    signal = self._hold_signal("hold", price, current_net)

            signals[sym] = signal
            self._append_price(window, self.current_time, price)

        return signals

    def _entry_signal(self, sym: str, bar: dict, price: float, window: Deque[tuple[datetime, float]]) -> dict | None:
        if len(window) < self.min_ticks_in_window:
            return self._hold_signal("warming_up", price, 0, history_len=len(window))

        tick_size = self._tick_size(sym)
        prices = [item_price for _, item_price in window]
        previous_high = max(prices)
        previous_low = min(prices)
        range_ticks = (previous_high - previous_low) / tick_size
        if range_ticks < self.min_range_ticks:
            return None

        if float(bar.get("tick_volume", 0.0) or 0.0) < self.min_tick_volume:
            return None

        upper_break = previous_high + self.breakout_ticks * tick_size
        lower_break = previous_low - self.breakout_ticks * tick_size
        if price >= upper_break:
            ratio = self._directional_ratio(window, direction=1)
            if ratio >= self.min_directional_ratio and self._imbalance_ok(bar, direction=1):
                entry_direction = 1 if self.breakout_mode == "follow" else -1
                return self._open_symbol_signal(
                    sym,
                    entry_direction,
                    f"rolling_high_breakout_{self.breakout_mode}",
                    price=price,
                    previous_high=previous_high,
                    range_ticks=range_ticks,
                    directional_ratio=ratio,
                )
        elif price <= lower_break:
            ratio = self._directional_ratio(window, direction=-1)
            if ratio >= self.min_directional_ratio and self._imbalance_ok(bar, direction=-1):
                entry_direction = -1 if self.breakout_mode == "follow" else 1
                return self._open_symbol_signal(
                    sym,
                    entry_direction,
                    f"rolling_low_breakout_{self.breakout_mode}",
                    price=price,
                    previous_low=previous_low,
                    range_ticks=range_ticks,
                    directional_ratio=ratio,
                )
        return None

    def _directional_ratio(self, window: Deque[tuple[datetime, float]], direction: int) -> float:
        cutoff = self.current_time.timestamp() - self.confirm_window_seconds
        total = 0
        same_direction = 0
        previous = None
        for tick_time, price in window:
            if tick_time.timestamp() < cutoff:
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

    def _imbalance_ok(self, bar: dict, direction: int) -> bool:
        if not self.use_imbalance_filter:
            return True
        bid_volume = bar.get("bid_volume_1")
        ask_volume = bar.get("ask_volume_1")
        if bid_volume is None or ask_volume is None or pd.isna(bid_volume) or pd.isna(ask_volume):
            return True
        total = float(bid_volume) + float(ask_volume)
        if total <= 0:
            return True
        imbalance = (float(bid_volume) - float(ask_volume)) / total
        return imbalance >= self.imbalance_threshold if direction > 0 else imbalance <= -self.imbalance_threshold

    def _append_price(self, window: Deque[tuple[datetime, float]], current_time: datetime, price: float):
        if not window or current_time >= window[-1][0]:
            window.append((current_time, price))
            return
        window.clear()
        window.append((current_time, price))

    def _trim_window(self, window: Deque[tuple[datetime, float]], current_time: datetime):
        while window and self._seconds_between(current_time, window[0][0]) > self.breakout_window_seconds:
            window.popleft()
