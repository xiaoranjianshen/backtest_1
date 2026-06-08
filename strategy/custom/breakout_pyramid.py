# -*- coding: utf-8 -*-
"""
Breakout strategy with pyramiding.

This is a beginner-friendly example for custom strategies.

Logic:
- If close breaks above the previous N-bar high, increase long target lots.
- If close breaks below the previous N-bar low, increase short target lots when allowed.
- Orders are submitted after the current bar is known, so fills happen on the next bar.
"""
import pandas as pd

from strategy.base import PortfolioTemplate


class BreakoutPyramidStrategy(PortfolioTemplate):
    def __init__(
        self,
        broker,
        account,
        symbol,
        lookback: int = 20,
        step_volume: int = 5,
        max_volume: int = 20,
        allow_short: bool = True,
    ):
        super().__init__(broker, account, symbols=[symbol])
        self.symbol = symbol.lower()
        self.lookback = int(lookback)
        self.step_volume = int(step_volume)
        self.max_volume = int(max_volume)
        self.allow_short = bool(allow_short)
        self.history = []

        if self.lookback <= 1:
            raise ValueError("lookback must be greater than 1")
        if self.step_volume <= 0:
            raise ValueError("step_volume must be positive")
        if self.max_volume <= 0:
            raise ValueError("max_volume must be positive")

    def on_init(self):
        print(
            f"[BreakoutPyramid] symbol={self.symbol} | lookback={self.lookback} | "
            f"step={self.step_volume} | max={self.max_volume} | allow_short={self.allow_short}"
        )
        self.inited = True

    def generate_target_portfolio(self, bar_data: dict) -> dict:
        if self.symbol not in bar_data:
            return {}

        bar = bar_data[self.symbol]
        close = bar.get("close")
        high = bar.get("high")
        low = bar.get("low")

        if pd.isna(close) or pd.isna(high) or pd.isna(low):
            return {}

        # Use only previous bars for breakout levels. The current bar is appended after the signal.
        if len(self.history) < self.lookback:
            self.history.append({"close": close, "high": high, "low": low})
            return {}

        recent = self.history[-self.lookback:]
        prev_high = max(item["high"] for item in recent)
        prev_low = min(item["low"] for item in recent)
        current_net = self.get_net_position(self.symbol)
        target_net = current_net

        if close > prev_high:
            base = max(current_net, 0)
            target_net = min(base + self.step_volume, self.max_volume)
        elif close < prev_low:
            if self.allow_short:
                base = min(current_net, 0)
                target_net = max(base - self.step_volume, -self.max_volume)
            else:
                target_net = 0

        self.history.append({"close": close, "high": high, "low": low})
        return {self.symbol: target_net}
