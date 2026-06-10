# -*- coding: utf-8 -*-
"""
GeneralMultiMAStrategy

Configurable multi-symbol moving-average example based on GeneralSignalStrategy.
The strategy only produces direction signals. Sizing and execution are configured
in the demo runner.
"""
import pandas as pd

from strategy.general_template import GeneralSignalStrategy


class GeneralMultiMAStrategy(GeneralSignalStrategy):
    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        fast_window: int = 10,
        slow_window: int = 30,
        **kwargs,
    ):
        if target_symbols is None:
            target_symbols = [symbol]

        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )

        self.fast_window = int(fast_window)
        self.slow_window = int(slow_window)
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("fast_window 和 slow_window 必须为正整数")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window 必须小于 slow_window")

        self.history = {sym: [] for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy GeneralMultiMA] fast={self.fast_window} | slow={self.slow_window} | "
            f"symbols={len(self.symbols)}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}

        for sym in self.symbols:
            if sym not in bar_data or pd.isna(bar_data[sym].get("close", pd.NA)):
                continue

            close_price = bar_data[sym]["close"]
            self.history[sym].append(close_price)
            if len(self.history[sym]) > self.slow_window + 1:
                self.history[sym].pop(0)

            if len(self.history[sym]) < self.slow_window + 1:
                signals[sym] = {
                    "signal": None,
                    "reason": "warming_up",
                    "metrics": {
                        "close": close_price,
                        "history_len": len(self.history[sym]),
                    },
                }
                continue

            prices = self.history[sym]
            fast_ma_curr = sum(prices[-self.fast_window:]) / self.fast_window
            slow_ma_curr = sum(prices[-self.slow_window:]) / self.slow_window
            fast_ma_prev = sum(prices[-(self.fast_window + 1):-1]) / self.fast_window
            slow_ma_prev = sum(prices[-(self.slow_window + 1):-1]) / self.slow_window

            golden_cross = fast_ma_prev <= slow_ma_prev and fast_ma_curr > slow_ma_curr
            death_cross = fast_ma_prev >= slow_ma_prev and fast_ma_curr < slow_ma_curr

            if golden_cross:
                direction = 1
                reason = "golden_cross"
            elif death_cross:
                direction = -1
                reason = "death_cross"
            else:
                direction = None
                reason = "hold"

            signals[sym] = {
                "signal": direction,
                "reason": reason,
                "metrics": {
                    "close": close_price,
                    "fast_ma": fast_ma_curr,
                    "slow_ma": slow_ma_curr,
                    "fast_ma_prev": fast_ma_prev,
                    "slow_ma_prev": slow_ma_prev,
                },
            }

        return signals
