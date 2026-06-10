# -*- coding: utf-8 -*-
"""
Dual moving-average signal strategy.

The strategy only emits standard signal intents. Position sizing, order
execution, partial exits, and reverse rules are handled by GeneralSignalStrategy.
"""
import pandas as pd

from strategy.general_template import GeneralSignalStrategy


class DualMAStrategy(GeneralSignalStrategy):
    def __init__(
        self,
        broker,
        account,
        symbol,
        fast_window=10,
        slow_window=30,
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
        self.fast_window = int(fast_window)
        self.slow_window = int(slow_window)
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("fast_window 和 slow_window 必须为正整数")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window 必须小于 slow_window")

        self.close_history = []

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy DualMA] symbol={self.symbol} | "
            f"fast={self.fast_window} | slow={self.slow_window}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        if self.symbol not in bar_data or pd.isna(bar_data[self.symbol].get("close", pd.NA)):
            return {}

        close_price = bar_data[self.symbol]["close"]
        self.close_history.append(close_price)
        if len(self.close_history) > self.slow_window + 1:
            self.close_history.pop(0)

        if len(self.close_history) < self.slow_window + 1:
            return {
                self.symbol: {
                    "signal": None,
                    "reason": "warming_up",
                    "metrics": {"close": close_price, "history_len": len(self.close_history)},
                }
            }

        prices = self.close_history
        fast_ma_curr = sum(prices[-self.fast_window:]) / self.fast_window
        slow_ma_curr = sum(prices[-self.slow_window:]) / self.slow_window
        fast_ma_prev = sum(prices[-(self.fast_window + 1):-1]) / self.fast_window
        slow_ma_prev = sum(prices[-(self.slow_window + 1):-1]) / self.slow_window

        golden_cross = fast_ma_prev <= slow_ma_prev and fast_ma_curr > slow_ma_curr
        death_cross = fast_ma_prev >= slow_ma_prev and fast_ma_curr < slow_ma_curr

        if golden_cross:
            signal = 1
            reason = "golden_cross"
        elif death_cross:
            signal = -1
            reason = "death_cross"
        else:
            signal = None
            reason = "hold"

        return {
            self.symbol: {
                "signal": signal,
                "reason": reason,
                "metrics": {
                    "close": close_price,
                    "fast_ma": fast_ma_curr,
                    "slow_ma": slow_ma_curr,
                    "fast_ma_prev": fast_ma_prev,
                    "slow_ma_prev": slow_ma_prev,
                },
            }
        }
