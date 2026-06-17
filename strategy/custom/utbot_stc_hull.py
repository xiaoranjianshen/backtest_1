# -*- coding: utf-8 -*-
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque

import numpy as np
import pandas as pd

from config import pure_product_code
from strategy.general_template import GeneralSignalStrategy


@dataclass
class _EntryState:
    direction: int
    price: float
    time: datetime
    bar_index: int


class UTBotSTCHullStrategy(GeneralSignalStrategy):
    """
    Bar-based UT Bot + STC + Hull strategy.

    This strategy is designed for 5-minute OHLCV data. It reads completed bars
    directly instead of rebuilding 5-minute bars from ticks. Orders generated on
    the current completed bar are filled by the engine on the next bar.
    """

    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        hma_length=55,
        atr_period=10,
        ut_key_value=1.0,
        stc_length=12,
        stc_fast=26,
        stc_slow=50,
        stc_factor=0.5,
        stc_long_max=35.0,
        stc_short_min=65.0,
        require_price_above_hull=True,
        exit_on_opposite_signal=True,
        take_profit_ticks=24.0,
        stop_loss_ticks=16.0,
        max_hold_bars=36,
        cooldown_bars=3,
        max_entries_per_symbol_per_day=20,
        min_completed_bars=None,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )
        self.hma_length = int(hma_length)
        self.atr_period = int(atr_period)
        self.ut_key_value = float(ut_key_value)
        self.stc_length = int(stc_length)
        self.stc_fast = int(stc_fast)
        self.stc_slow = int(stc_slow)
        self.stc_factor = float(stc_factor)
        self.stc_long_max = float(stc_long_max)
        self.stc_short_min = float(stc_short_min)
        self.require_price_above_hull = bool(require_price_above_hull)
        self.exit_on_opposite_signal = bool(exit_on_opposite_signal)
        self.take_profit_ticks = float(take_profit_ticks)
        self.stop_loss_ticks = float(stop_loss_ticks)
        self.max_hold_bars = int(max_hold_bars)
        self.cooldown_bars = int(cooldown_bars)
        self.max_entries_per_symbol_per_day = (
            None if max_entries_per_symbol_per_day in (None, "", 0) else int(max_entries_per_symbol_per_day)
        )

        if self.hma_length < 2:
            raise ValueError("hma_length must be at least 2")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive")
        if min(self.stc_length, self.stc_fast, self.stc_slow) < 1:
            raise ValueError("STC parameters must be positive")
        if not 0 < self.stc_factor <= 1:
            raise ValueError("stc_factor must be in (0, 1]")
        if self.take_profit_ticks <= 0:
            raise ValueError("take_profit_ticks must be positive")
        if self.stop_loss_ticks <= 0:
            raise ValueError("stop_loss_ticks must be positive")
        if self.max_hold_bars <= 0:
            raise ValueError("max_hold_bars must be positive")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars cannot be negative")

        indicator_warmup = max(
            self.hma_length + int(math.sqrt(self.hma_length)) + 2,
            self.atr_period + 2,
            self.stc_slow + self.stc_length * 2 + 5,
        )
        self.min_completed_bars = int(min_completed_bars or indicator_warmup)
        self.bars: dict[str, Deque[dict]] = {
            sym: deque(maxlen=max(self.min_completed_bars * 4, 500)) for sym in self.symbols
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
            f"[Strategy UTBotSTCHull] symbols={self.symbols} | "
            f"HMA={self.hma_length} | UT={self.ut_key_value:g}/{self.atr_period} | "
            f"STC={self.stc_length}/{self.stc_fast}/{self.stc_slow}/{self.stc_factor:g}"
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

            self.bars[sym].append(bar)
            self.bar_count[sym] += 1

            close_price = bar["close"]
            current_net = self.get_net_position(sym)
            self._sync_entry_state(sym, current_net, close_price)

            setup = self._evaluate_setup(sym)
            if current_net != 0:
                signals[sym] = self._position_signal(sym, close_price, current_net, setup)
            else:
                signals[sym] = self._flat_signal(sym, close_price, current_net, setup)
        return signals

    def _flat_signal(self, sym: str, price: float, current_net: int, setup: dict | None) -> dict:
        if setup is None:
            return self._hold_signal("hold", price, current_net)

        blocked = self._entry_block_reason(sym)
        if blocked:
            return self._hold_signal(blocked, price, current_net)

        direction = int(setup["direction"])
        self._register_entry(sym)
        reason = "utbot_stc_hull_long" if direction > 0 else "utbot_stc_hull_short"
        return {
            "signal": direction,
            "position_mode": "target",
            "reason": reason,
            "metrics": self._metrics(sym, price, current_net, setup),
        }

    def _position_signal(self, sym: str, price: float, current_net: int, setup: dict | None) -> dict:
        metrics = self._metrics(sym, price, current_net, setup)
        direction = 1 if current_net > 0 else -1

        if self.exit_on_opposite_signal and setup is not None and int(setup["direction"]) == -direction:
            return self._exit_signal("opposite_utbot_stc_hull_signal", metrics)

        state = self.entry_state.get(sym)
        if state is not None:
            tick_size = self._tick_size(sym)
            pnl_ticks = ((price - state.price) / tick_size) * direction
            metrics["pnl_ticks"] = pnl_ticks
            metrics["holding_bars"] = self.bar_count[sym] - state.bar_index
            if pnl_ticks >= self.take_profit_ticks:
                return self._exit_signal("take_profit", metrics)
            if pnl_ticks <= -self.stop_loss_ticks:
                return self._exit_signal("stop_loss", metrics)
            if metrics["holding_bars"] >= self.max_hold_bars:
                return self._exit_signal("time_exit", metrics)

        return {"signal": None, "reason": "holding", "metrics": metrics}

    def _evaluate_setup(self, sym: str) -> dict | None:
        bars = list(self.bars[sym])
        if len(bars) < self.min_completed_bars:
            self.last_snapshot[sym] = {
                "completed_bars": len(bars),
                "warmup_required": self.min_completed_bars,
            }
            return None

        snapshot = self._indicator_snapshot(bars)
        self.last_snapshot[sym] = snapshot
        if snapshot is None:
            return None

        close = snapshot["close"]
        hma = snapshot["hma"]
        prev_hma = snapshot["prev_hma"]
        hull_up = hma > prev_hma and (not self.require_price_above_hull or close > hma)
        hull_down = hma < prev_hma and (not self.require_price_above_hull or close < hma)
        stc_up_from_low = snapshot["stc"] > snapshot["prev_stc"] and snapshot["prev_stc"] <= self.stc_long_max
        stc_down_from_high = snapshot["stc"] < snapshot["prev_stc"] and snapshot["prev_stc"] >= self.stc_short_min

        if hull_up and snapshot["ut_signal"] > 0 and stc_up_from_low:
            return {"direction": 1, **snapshot}
        if hull_down and snapshot["ut_signal"] < 0 and stc_down_from_high:
            return {"direction": -1, **snapshot}
        return None

    def _indicator_snapshot(self, bars: list[dict]) -> dict | None:
        closes = np.array([bar["close"] for bar in bars], dtype=float)
        hma = self._hma_at(closes, len(closes) - 1)
        prev_hma = self._hma_at(closes, len(closes) - 2)
        ut_signal, ut_stop = self._ut_bot_signal(bars)
        stc, prev_stc = self._stc_pair(closes)
        if None in (hma, prev_hma, ut_signal, ut_stop, stc, prev_stc):
            return None
        return {
            "bar_time": bars[-1]["datetime"],
            "close": float(closes[-1]),
            "hma": float(hma),
            "prev_hma": float(prev_hma),
            "ut_signal": int(ut_signal),
            "ut_stop": float(ut_stop),
            "stc": float(stc),
            "prev_stc": float(prev_stc),
            "completed_bars": len(bars),
        }

    def _ut_bot_signal(self, bars: list[dict]) -> tuple[int | None, float | None]:
        if len(bars) < self.atr_period + 2:
            return None, None

        closes = [float(bar["close"]) for bar in bars]
        atr_values = self._atr_series(bars)
        stop = None
        prev_stop = None
        prev_close = None
        signal = 0

        for idx, close in enumerate(closes):
            atr = atr_values[idx]
            if atr is None:
                prev_close = close
                continue
            loss = self.ut_key_value * atr
            if stop is None:
                stop = close - loss
                prev_stop = stop
                prev_close = close
                continue

            assert prev_stop is not None and prev_close is not None
            if close > prev_stop and prev_close > prev_stop:
                stop = max(prev_stop, close - loss)
            elif close < prev_stop and prev_close < prev_stop:
                stop = min(prev_stop, close + loss)
            elif close > prev_stop:
                stop = close - loss
            else:
                stop = close + loss

            signal = 1 if close > stop and prev_close <= prev_stop else -1 if close < stop and prev_close >= prev_stop else 0
            prev_stop = stop
            prev_close = close

        return signal, stop

    def _atr_series(self, bars: list[dict]) -> list[float | None]:
        true_ranges = []
        previous_close = None
        for bar in bars:
            high = float(bar["high"])
            low = float(bar["low"])
            if previous_close is None:
                true_range = high - low
            else:
                true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
            true_ranges.append(true_range)
            previous_close = float(bar["close"])

        atr_values: list[float | None] = [None] * len(true_ranges)
        if len(true_ranges) < self.atr_period:
            return atr_values

        atr = sum(true_ranges[: self.atr_period]) / self.atr_period
        atr_values[self.atr_period - 1] = atr
        for idx in range(self.atr_period, len(true_ranges)):
            atr = (atr * (self.atr_period - 1) + true_ranges[idx]) / self.atr_period
            atr_values[idx] = atr
        return atr_values

    def _stc_pair(self, closes: np.ndarray) -> tuple[float | None, float | None]:
        fast_ema = self._ema_series(closes, self.stc_fast)
        slow_ema = self._ema_series(closes, self.stc_slow)
        macd = [None if fast is None or slow is None else fast - slow for fast, slow in zip(fast_ema, slow_ema)]
        first = self._smoothed_stochastic(macd, self.stc_length, self.stc_factor)
        second = self._smoothed_stochastic(first, self.stc_length, self.stc_factor)
        valid = [value for value in second if value is not None]
        if len(valid) < 2:
            return None, None
        return valid[-1], valid[-2]

    @staticmethod
    def _ema_series(values: np.ndarray, period: int) -> list[float | None]:
        result: list[float | None] = [None] * len(values)
        if len(values) < period:
            return result
        alpha = 2.0 / (period + 1.0)
        ema = float(values[:period].mean())
        result[period - 1] = ema
        for idx in range(period, len(values)):
            ema = alpha * float(values[idx]) + (1.0 - alpha) * ema
            result[idx] = ema
        return result

    @staticmethod
    def _smoothed_stochastic(values: list[float | None], length: int, factor: float) -> list[float | None]:
        result: list[float | None] = [None] * len(values)
        previous = None
        for idx, value in enumerate(values):
            if value is None:
                continue
            window = [item for item in values[max(0, idx - length + 1) : idx + 1] if item is not None]
            if len(window) < length:
                continue
            low = min(window)
            high = max(window)
            raw = 50.0 if high == low else 100.0 * (value - low) / (high - low)
            smoothed = raw if previous is None else previous + factor * (raw - previous)
            result[idx] = smoothed
            previous = smoothed
        return result

    def _hma_at(self, values: np.ndarray, end_idx: int) -> float | None:
        if end_idx < 0:
            return None
        half_length = max(1, self.hma_length // 2)
        sqrt_length = max(1, int(round(math.sqrt(self.hma_length))))
        if end_idx + 1 < self.hma_length + sqrt_length - 1:
            return None

        diff_values = []
        for idx in range(end_idx - sqrt_length + 1, end_idx + 1):
            if idx + 1 < self.hma_length:
                return None
            half = self._wma(values[idx - half_length + 1 : idx + 1])
            full = self._wma(values[idx - self.hma_length + 1 : idx + 1])
            diff_values.append(2.0 * half - full)
        return self._wma(np.array(diff_values, dtype=float))

    @staticmethod
    def _wma(values: np.ndarray) -> float:
        weights = np.arange(1, len(values) + 1, dtype=float)
        return float(np.dot(values, weights) / weights.sum())

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

    def _metrics(self, sym: str, price: float, current_net: int, setup: dict | None) -> dict:
        snapshot = self.last_snapshot.get(sym) or {}
        metrics = {
            "price": price,
            "current_net": current_net,
            **{f"indicator_{key}": value for key, value in snapshot.items()},
        }
        if setup:
            metrics.update({key: value for key, value in setup.items() if key != "direction"})
        return metrics

    @staticmethod
    def _hold_signal(reason: str, price: float, current_net: int) -> dict:
        return {"signal": None, "reason": reason, "metrics": {"price": price, "current_net": current_net}}

    @staticmethod
    def _exit_signal(reason: str, metrics: dict) -> dict:
        return {"signal": 0, "position_mode": "flat", "reason": reason, "metrics": metrics}

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

    def _tick_size(self, sym: str) -> float:
        raw_code = pure_product_code(sym)
        meta = self.account.fee_model._get_meta_data(raw_code)
        return float(meta["tick_size"])
