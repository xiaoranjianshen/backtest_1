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
        symbol="multi",
        target_symbols=None,
        fast_window=10,
        slow_window=30,
        **kwargs,
    ):
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

        self.close_history = {sym: [] for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(
            f"[Strategy DualMA] symbols={self.symbols} | "
            f"fast={self.fast_window} | slow={self.slow_window}"
        )

    def generate_signals(self, bar_data: dict) -> dict:
        signals = {}
        for symbol in self.symbols:
            bar = bar_data.get(symbol)
            if not bar or pd.isna(bar.get("close", pd.NA)):
                continue

            close_price = float(bar["close"])
            history = self.close_history.setdefault(symbol, [])
            history.append(close_price)
            if len(history) > self.slow_window + 1:
                history.pop(0)

            if len(history) < self.slow_window + 1:
                signals[symbol] = {
                    "signal": None,
                    "reason": "warming_up",
                    "metrics": {"close": close_price, "history_len": len(history)},
                }
                continue

            fast_ma_curr = sum(history[-self.fast_window:]) / self.fast_window
            slow_ma_curr = sum(history[-self.slow_window:]) / self.slow_window
            fast_ma_prev = sum(history[-(self.fast_window + 1):-1]) / self.fast_window
            slow_ma_prev = sum(history[-(self.slow_window + 1):-1]) / self.slow_window

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

            signals[symbol] = {
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

        return signals
