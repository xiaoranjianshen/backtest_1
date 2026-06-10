# -*- coding: utf-8 -*-
"""
General signal strategy template.

New strategies can inherit this class and only implement generate_signals().
Strategies emit standardized signal intents. Sizing, market/limit execution,
reverse handling, partial exits, and order routing are configured by the engine.
"""
from datetime import datetime
from typing import Iterable

from strategy.base import BaseStrategy
from strategy.common.rebalancer import SignalRebalancer
from strategy.common.types import coerce_execution_config


class GeneralSignalStrategy(BaseStrategy):
    def __init__(
        self,
        broker,
        account,
        symbol="multi",
        target_symbols: Iterable[str] | None = None,
        sizing: dict | None = None,
        execution: dict | None = None,
        exit: dict | None = None,
        record_signals: bool = True,
    ):
        symbols = self._resolve_symbols(symbol, target_symbols)
        super().__init__(broker, account, symbols=symbols)

        self.sizing_config = sizing or {"mode": "fixed_volume", "value": 1}
        self.execution_config = execution or {"order_type": "market"}
        self.exit_config = exit or {"close_pct": 1.0, "allow_reverse": True}
        self.record_signals = bool(record_signals)
        self.signal_records = []

        execution_cfg = coerce_execution_config(self.execution_config)
        self.broker.fee_model.default_slippage_ticks = float(execution_cfg.slippage_ticks)
        self.account.fee_model.default_slippage_ticks = float(execution_cfg.slippage_ticks)

        self.rebalancer = SignalRebalancer(
            strategy=self,
            sizing=self.sizing_config,
            execution=self.execution_config,
            exit_config=self.exit_config,
        )

    @staticmethod
    def _resolve_symbols(symbol, target_symbols):
        if target_symbols is not None:
            return [sym.lower() for sym in target_symbols]
        if isinstance(symbol, str):
            return [symbol.lower()]
        return [sym.lower() for sym in symbol]

    def on_init(self):
        print(
            f"[Strategy GeneralSignal] symbols={self.symbols} | "
            f"sizing={self.sizing_config} | execution={self.execution_config} | exit={self.exit_config}"
        )
        self.inited = True

    def on_bar(self, current_time: datetime, bar_data: dict):
        self.current_time = current_time
        signals = self.generate_signals(bar_data) or {}
        records = self.rebalancer.rebalance(signals, bar_data)

        if self.record_signals:
            for record in records:
                self.signal_records.append({"datetime": current_time, **record})

    def generate_signals(self, bar_data: dict) -> dict:
        raise NotImplementedError("子类必须实现 generate_signals(bar_data)，并返回标准 signal intent 字典")
