# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Deque

import numpy as np

from strategy.custom.tick_common import TickScalpingBase


class TickVWAPReversionStrategy(TickScalpingBase):
    """
    Tick-level rolling VWAP reversion strategy.

    The strategy compares the current tick with a rolling VWAP/standard
    deviation window. Entry uses only the completed window before the current
    tick plus the current observable price.
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        lookback_seconds=120.0,
        entry_z=1.8,
        exit_z=0.25,
        min_std_ticks=1.5,
        min_deviation_ticks=5.0,
        min_ticks_in_window=60,
        require_turn_tick=True,
        turn_ticks=1.0,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.lookback_seconds = float(lookback_seconds)
        self.entry_z = float(entry_z)
        self.exit_z = float(exit_z)
        self.min_std_ticks = float(min_std_ticks)
        self.min_deviation_ticks = float(min_deviation_ticks)
        self.min_ticks_in_window = int(min_ticks_in_window)
        self.require_turn_tick = bool(require_turn_tick)
        self.turn_ticks = float(turn_ticks)

        if self.lookback_seconds <= 0:
            raise ValueError("lookback_seconds must be positive")
        if self.entry_z <= 0:
            raise ValueError("entry_z must be positive")
        if self.exit_z < 0:
            raise ValueError("exit_z cannot be negative")

        self.price_windows: dict[str, Deque[tuple[datetime, float, float]]] = {sym: deque() for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy TickVWAPReversion] symbols={self.symbols} | lookback={self.lookback_seconds:g}s | "
            f"entry_z={self.entry_z:g} | exit_z={self.exit_z:g}"
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
            stats = self._rolling_stats(sym, window)

            current_net = self.get_net_position(sym)
            self._sync_entry_state(sym, current_net, price)

            signal = None
            if current_net != 0:
                signal = self._position_exit_signal(sym, bar, price, current_net, stats)
                if signal is None:
                    metrics = self._position_metrics(sym, price, current_net, **self._stats_metrics(stats, price))
                    signal = {"signal": None, "reason": "holding", "metrics": metrics}
            else:
                signal = self._entry_block_signal(sym, bar, price, current_net)
                if signal is None:
                    signal = self._entry_signal(sym, price, window, stats)
                if signal is None:
                    signal = self._hold_signal("hold", price, current_net, **self._stats_metrics(stats, price))

            signals[sym] = signal
            self._append_price(window, self.current_time, price, float(bar.get("tick_volume", 0.0) or 0.0))

        return signals

    def _entry_signal(
        self,
        sym: str,
        price: float,
        window: Deque[tuple[datetime, float, float]],
        stats: dict | None,
    ) -> dict | None:
        if stats is None:
            return self._hold_signal("warming_up", price, 0, history_len=len(window))

        tick_size = self._tick_size(sym)
        zscore = (price - stats["vwap"]) / stats["std"]
        deviation_ticks = abs(price - stats["vwap"]) / tick_size
        std_ticks = stats["std"] / tick_size
        if std_ticks < self.min_std_ticks or deviation_ticks < self.min_deviation_ticks:
            return None

        if zscore >= self.entry_z:
            if self._turn_confirmed(sym, window, price, direction=-1):
                return self._open_symbol_signal(
                    sym,
                    -1,
                    "vwap_high_reversion",
                    price=price,
                    vwap=stats["vwap"],
                    zscore=zscore,
                    deviation_ticks=deviation_ticks,
                    std_ticks=std_ticks,
                )
        elif zscore <= -self.entry_z:
            if self._turn_confirmed(sym, window, price, direction=1):
                return self._open_symbol_signal(
                    sym,
                    1,
                    "vwap_low_reversion",
                    price=price,
                    vwap=stats["vwap"],
                    zscore=zscore,
                    deviation_ticks=deviation_ticks,
                    std_ticks=std_ticks,
                )
        return None

    def _position_exit_signal(self, sym: str, bar: dict, price: float, current_net: int, stats: dict | None) -> dict | None:
        risk = self._risk_exit_signal(sym, bar, price, current_net, **self._stats_metrics(stats, price))
        if risk is not None:
            return risk
        if stats is None:
            return None

        zscore = (price - stats["vwap"]) / stats["std"]
        metrics = self._position_metrics(sym, price, current_net, **self._stats_metrics(stats, price))
        metrics["zscore"] = zscore

        if current_net > 0 and zscore >= -self.exit_z:
            return self._exit_order_signal("vwap_mean_exit", metrics)
        if current_net < 0 and zscore <= self.exit_z:
            return self._exit_order_signal("vwap_mean_exit", metrics)
        return None

    def _rolling_stats(self, sym: str, window: Deque[tuple[datetime, float, float]]) -> dict | None:
        if len(window) < self.min_ticks_in_window:
            return None

        prices = np.array([item[1] for item in window], dtype=float)
        volumes = np.array([max(0.0, item[2]) for item in window], dtype=float)
        if volumes.sum() > 0:
            center = float(np.average(prices, weights=volumes))
        else:
            center = float(prices.mean())
        std = float(prices.std(ddof=0))
        tick_size = self._tick_size(sym)
        if std <= 0:
            std = tick_size
        return {"vwap": center, "std": std}

    def _stats_metrics(self, stats: dict | None, price: float) -> dict:
        if stats is None:
            return {}
        return {
            "vwap": stats["vwap"],
            "std": stats["std"],
            "zscore": (price - stats["vwap"]) / stats["std"],
        }

    def _turn_confirmed(self, sym: str, window: Deque[tuple[datetime, float, float]], price: float, direction: int) -> bool:
        if not self.require_turn_tick:
            return True
        if not window:
            return False
        tick_size = self._tick_size(sym)
        previous_price = window[-1][1]
        required_change = self.turn_ticks * tick_size
        change = price - previous_price
        return change >= required_change if direction > 0 else change <= -required_change

    def _append_price(self, window: Deque[tuple[datetime, float, float]], current_time: datetime, price: float, volume: float):
        if not window or current_time >= window[-1][0]:
            window.append((current_time, price, volume))
            return
        window.clear()
        window.append((current_time, price, volume))

    def _trim_window(self, window: Deque[tuple[datetime, float, float]], current_time: datetime):
        while window and self._seconds_between(current_time, window[0][0]) > self.lookback_seconds:
            window.popleft()
