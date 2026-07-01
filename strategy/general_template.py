# -*- coding: utf-8 -*-
"""
General signal strategy template.

New strategies can inherit this class and only implement generate_signals().
Strategies emit standardized signal intents. Sizing, market/limit execution,
reverse handling, partial exits, and order routing are configured by the engine.
"""
import json
from datetime import datetime
from typing import Iterable

from strategy.base import BaseStrategy
from strategy.common.rebalancer import SignalRebalancer
from strategy.common.types import coerce_execution_config, normalize_signal


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
        record_signal_holds: bool = False,
    ):
        symbols = self._resolve_symbols(symbol, target_symbols)
        super().__init__(broker, account, symbols=symbols)

        self.sizing_config = sizing or {"mode": "fixed_volume", "value": 1}
        self.execution_config = execution or {"order_type": "market"}
        self.exit_config = exit or {"close_pct": 1.0, "allow_reverse": True}
        self.record_signals = bool(record_signals)
        self.record_signal_holds = bool(record_signal_holds)
        self.raw_signal_records = []
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
        if self.record_signals:
            self._record_raw_signal_events(current_time, signals, bar_data)
        records = self.rebalancer.rebalance(signals, bar_data)

        if self.record_signals:
            for record in records:
                self.signal_records.append({"datetime": current_time, **record})

    def generate_signals(self, bar_data: dict) -> dict:
        raise NotImplementedError("子类必须实现 generate_signals(bar_data)，并返回标准 signal intent 字典")

    def _record_raw_signal_events(self, current_time: datetime, signals: dict, bar_data: dict):
        for sym, raw_signal in (signals or {}).items():
            try:
                intent = normalize_signal(raw_signal)
            except Exception as exc:
                self.raw_signal_records.append({
                    "datetime": current_time,
                    "symbol": sym,
                    "signal": None,
                    "reason": f"normalize_error:{exc}",
                })
                continue

            is_hold = (
                intent.direction is None
                and intent.target_net is None
                and intent.target_weight is None
                and intent.target_margin_pct is None
                and intent.risk_pct is None
                and intent.position_mode is None
            )
            if is_hold and not self.record_signal_holds:
                continue

            bar = bar_data.get(sym, {}) if isinstance(bar_data, dict) else {}
            record = {
                "datetime": current_time,
                "symbol": sym,
                "signal": intent.direction,
                "position_mode": intent.position_mode,
                "reason": intent.reason,
                "current_net": self.get_net_position(sym),
                "price": bar.get("close"),
                "size_scale": intent.size_scale,
                "signal_score": intent.signal_score,
                "target_volume": intent.target_volume,
                "target_net": intent.target_net,
                "target_pct": intent.target_pct,
                "target_weight": intent.target_weight,
                "target_margin_pct": intent.target_margin_pct,
                "delta_volume": intent.delta_volume,
                "delta_pct": intent.delta_pct,
                "close_pct": intent.close_pct,
                "close_volume": intent.close_volume,
                "risk_pct": intent.risk_pct,
                "stop_loss_ticks": intent.stop_loss_ticks,
                "stop_loss_price": intent.stop_loss_price,
            }

            metrics = intent.metrics or {}
            record["metrics_json"] = self._json_dumps(metrics)
            for key, value in metrics.items():
                if self._is_scalar(value):
                    record[f"metric_{key}"] = value

            for key, value in intent.extra.items():
                if self._is_scalar(value):
                    record[f"extra_{key}"] = value

            self.raw_signal_records.append(record)

    @staticmethod
    def _is_scalar(value) -> bool:
        return value is None or isinstance(value, (str, int, float, bool))

    @staticmethod
    def _json_dumps(value) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)
