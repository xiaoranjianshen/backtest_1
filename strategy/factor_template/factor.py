# -*- coding: utf-8 -*-
"""
General cross-sectional factor strategy template.

Subclasses implement calculate_weights(). Returned weights are treated as
relative signal strength; actual lots are produced by the general sizing policy.
"""
from datetime import datetime

import pandas as pd

from strategy.general_template import GeneralSignalStrategy


class FactorTemplate(GeneralSignalStrategy):
    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols=None,
        rebalance_period: int = 1,
        **kwargs,
    ):
        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            target_symbols=target_symbols,
            **kwargs,
        )

        self.rebalance_period = int(rebalance_period)
        if self.rebalance_period <= 0:
            raise ValueError("rebalance_period must be positive")
        self.bar_count = 0

    def on_init(self):
        super().on_init()
        print(f"[Strategy Factor] rebalance_period={self.rebalance_period}")

    def on_bar(self, current_time: datetime, bar_data: dict):
        self.current_time = current_time
        self.bar_count += 1

        if self.bar_count % self.rebalance_period != 0:
            return

        signals = self.generate_signals(bar_data) or {}
        records = self.rebalancer.rebalance(signals, bar_data)

        if self.record_signals:
            for record in records:
                self.signal_records.append({"datetime": current_time, **record})

    def generate_signals(self, bar_data: dict) -> dict:
        cross_section = {
            sym: data
            for sym, data in bar_data.items()
            if sym in self.symbols and data and not pd.isna(data.get("close", pd.NA))
        }

        if not cross_section:
            return {}

        target_weights = self.calculate_weights(cross_section) or {}
        max_abs_weight = max([abs(float(value)) for value in target_weights.values()] + [1.0])
        signals = {}

        symbols_to_check = set(cross_section) | {
            symbol for symbol in self.symbols if self.get_net_position(symbol) != 0
        }

        for sym in symbols_to_check:
            weight = float(target_weights.get(sym, 0.0))
            if weight > 0:
                signals[sym] = {
                    "signal": 1,
                    "position_mode": "target",
                    "size_scale": abs(weight) / max_abs_weight,
                    "reason": "factor_long",
                    "metrics": {"factor_weight": weight},
                }
            elif weight < 0:
                signals[sym] = {
                    "signal": -1,
                    "position_mode": "target",
                    "size_scale": abs(weight) / max_abs_weight,
                    "reason": "factor_short",
                    "metrics": {"factor_weight": weight},
                }
            elif self.get_net_position(sym) != 0:
                signals[sym] = {
                    "signal": 0,
                    "position_mode": "flat",
                    "reason": "factor_exit",
                    "metrics": {"factor_weight": 0.0},
                }

        return signals

    def calculate_weights(self, cross_section: dict) -> dict:
        raise NotImplementedError("子类必须实现 calculate_weights(cross_section)")
