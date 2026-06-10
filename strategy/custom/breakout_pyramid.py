# -*- coding: utf-8 -*-
"""
Breakout pyramiding signal strategy.

The strategy emits incremental trading intents. Base position size, execution
type, slippage, and exit behavior are controlled by the general strategy config.
"""
import pandas as pd

from strategy.general_template import GeneralSignalStrategy


class BreakoutPyramidStrategy(GeneralSignalStrategy):
    def __init__(
        self,
        broker,
        account,
        symbol,
        lookback: int = 20,
        add_scale: float = 1.0,
        max_position_scale: float = 4.0,
        allow_short: bool = True,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=[symbol],
            **kwargs,
        )

        self.symbol = self.symbols[0]
        self.lookback = int(lookback)
        self.add_scale = float(add_scale)
        self.max_position_scale = float(max_position_scale)
        self.allow_short = bool(allow_short)
        self.history = []

        if self.lookback <= 1:
            raise ValueError("lookback must be greater than 1")
        if self.add_scale <= 0:
            raise ValueError("add_scale must be positive")
        if self.max_position_scale <= 0:
            raise ValueError("max_position_scale must be positive")

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy BreakoutPyramid] symbol={self.symbol} | lookback={self.lookback} | "
            f"add_scale={self.add_scale:g} | max_position_scale={self.max_position_scale:g} | "
            f"allow_short={self.allow_short}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        if self.symbol not in bar_data:
            return {}

        bar = bar_data[self.symbol]
        close_price = bar.get("close")
        high_price = bar.get("high")
        low_price = bar.get("low")

        if pd.isna(close_price) or pd.isna(high_price) or pd.isna(low_price):
            return {}

        if len(self.history) < self.lookback:
            self.history.append({"close": close_price, "high": high_price, "low": low_price})
            return {
                self.symbol: {
                    "signal": None,
                    "reason": "warming_up",
                    "metrics": {"close": close_price, "history_len": len(self.history)},
                }
            }

        recent = self.history[-self.lookback:]
        prev_high = max(item["high"] for item in recent)
        prev_low = min(item["low"] for item in recent)

        signal = None
        position_mode = None
        reason = "hold"

        if close_price > prev_high:
            signal = 1
            position_mode = "delta"
            reason = "upside_breakout"
        elif close_price < prev_low:
            if self.allow_short:
                signal = -1
                position_mode = "delta"
                reason = "downside_breakout"
            else:
                signal = 0
                position_mode = "flat"
                reason = "downside_breakout_exit"

        self.history.append({"close": close_price, "high": high_price, "low": low_price})

        return {
            self.symbol: {
                "signal": signal,
                "position_mode": position_mode,
                "size_scale": self.add_scale,
                "max_position_scale": self.max_position_scale,
                "reason": reason,
                "metrics": {
                    "close": close_price,
                    "prev_high": prev_high,
                    "prev_low": prev_low,
                    "lookback": self.lookback,
                },
            }
        }
