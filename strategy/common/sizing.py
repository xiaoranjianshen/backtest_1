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

    def _normalize_volume(self, raw_volume: float) -> int:
        cfg = self.config
        lot = max(1, int(cfg.round_lot))
        volume = int(math.floor(raw_volume / lot) * lot)

        if raw_volume > 0 and volume < cfg.min_volume:
            volume = int(cfg.min_volume)

        if cfg.max_volume is not None:
            volume = min(volume, int(cfg.max_volume))

        return max(0, volume)

