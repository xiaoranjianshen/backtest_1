# -*- coding: utf-8 -*-
import math

import pandas as pd

from strategy.common.types import SizingConfig, coerce_sizing_config


class PositionSizer:
    """Convert sizing config into futures lots."""

    def __init__(self, config=None):
        self.config: SizingConfig = coerce_sizing_config(config)

    def calculate(self, symbol: str, price: float, account, current_prices: dict | None = None) -> int:
        if price is None or pd.isna(price) or price <= 0:
            return 0

        cfg = self.config
        mode = cfg.mode.lower()
        value = float(cfg.value)
        if value <= 0:
            return 0

        meta = account.fee_model._get_meta_data(symbol)
        multiplier = meta["multiplier"]
        margin_rate = meta["margin_rate"]
        margin_per_lot = price * multiplier * margin_rate
        notional_per_lot = price * multiplier

        if margin_per_lot <= 0 or notional_per_lot <= 0:
            return 0

        if mode == "fixed_volume":
            raw_volume = value
        elif mode == "fixed_margin":
            raw_volume = value / margin_per_lot
        elif mode == "fixed_notional":
            raw_volume = value / notional_per_lot
        elif mode == "capital_pct":
            raw_volume = (account.initial_capital * value) / margin_per_lot
        elif mode == "equity_pct":
            if current_prices:
                base_capital = account.get_total_equity(current_prices)
            else:
                base_capital = account.available + account.frozen_margin
            raw_volume = (base_capital * value) / margin_per_lot
        elif mode == "available_pct":
            raw_volume = (account.available * value) / margin_per_lot
        else:
            raise ValueError(f"Unsupported sizing mode: {cfg.mode}")

        return self._normalize_volume(raw_volume)

    def calculate_target_weight(
        self,
        symbol: str,
        price: float,
        account,
        current_prices: dict | None,
        target_weight: float,
    ) -> int:
        """Return lots for a target notional/equity weight."""
        meta = self._valid_meta(symbol, price, account)
        if meta is None:
            return 0
        equity = self._current_equity(account, current_prices)
        raw_volume = equity * abs(float(target_weight)) / meta["notional_per_lot"]
        return self._normalize_volume(raw_volume)

    def calculate_target_margin_pct(
        self,
        symbol: str,
        price: float,
        account,
        current_prices: dict | None,
        target_margin_pct: float,
    ) -> int:
        """Return lots for a target margin/equity percentage."""
        meta = self._valid_meta(symbol, price, account)
        if meta is None:
            return 0
        equity = self._current_equity(account, current_prices)
        raw_volume = equity * abs(float(target_margin_pct)) / meta["margin_per_lot"]
        return self._normalize_volume(raw_volume)

    def calculate_risk_pct(
        self,
        symbol: str,
        price: float,
        account,
        current_prices: dict | None,
        risk_pct: float,
        stop_loss_ticks: float | None = None,
        stop_loss_price: float | None = None,
    ) -> int:
        """Return lots so the configured stop distance risks a percentage of equity."""
        meta = self._valid_meta(symbol, price, account)
        if meta is None:
            return 0

        if stop_loss_price is not None:
            stop_distance = abs(float(price) - float(stop_loss_price))
        elif stop_loss_ticks is not None:
            stop_distance = abs(float(stop_loss_ticks)) * meta["tick_size"]
        else:
            return 0

        risk_per_lot = stop_distance * meta["multiplier"]
        if risk_per_lot <= 0:
            return 0

        equity = self._current_equity(account, current_prices)
        raw_volume = equity * abs(float(risk_pct)) / risk_per_lot
        return self._normalize_volume(raw_volume)

    @staticmethod
    def _current_equity(account, current_prices: dict | None = None) -> float:
        if current_prices:
            return float(account.get_total_equity(current_prices))
        return float(account.available + account.frozen_margin)

    @staticmethod
    def _valid_meta(symbol: str, price: float, account):
        if price is None or pd.isna(price) or price <= 0:
            return None

        meta = account.fee_model._get_meta_data(symbol)
        multiplier = float(meta["multiplier"])
        margin_rate = float(meta["margin_rate"])
        tick_size = float(meta.get("tick_size", 1.0))
        margin_per_lot = price * multiplier * margin_rate
        notional_per_lot = price * multiplier
        if margin_per_lot <= 0 or notional_per_lot <= 0 or multiplier <= 0:
            return None
        return {
            "multiplier": multiplier,
            "margin_rate": margin_rate,
            "tick_size": tick_size,
            "margin_per_lot": margin_per_lot,
            "notional_per_lot": notional_per_lot,
        }

    def _normalize_volume(self, raw_volume: float) -> int:
        cfg = self.config
        lot = max(1, int(cfg.round_lot))
        volume = int(math.floor(raw_volume / lot) * lot)

        if raw_volume > 0 and volume < cfg.min_volume:
            volume = int(cfg.min_volume)

        if cfg.max_volume is not None:
            volume = min(volume, int(cfg.max_volume))

        return max(0, volume)
