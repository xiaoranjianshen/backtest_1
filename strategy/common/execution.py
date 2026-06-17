# -*- coding: utf-8 -*-
import pandas as pd

from broker.order import Direction
from strategy.common.types import ExecutionConfig, coerce_execution_config


class ExecutionPolicy:
    """Choose market/limit order price from config."""

    def __init__(self, config=None):
        self.config: ExecutionConfig = coerce_execution_config(config)

    def get_order_prices(
        self,
        symbol: str,
        order_direction: Direction,
        bar: dict,
        account,
        explicit_limit_price: float | None = None,
    ) -> tuple[float, float]:
        cfg = self.config
        ref_price = self._reference_price(bar, cfg.price_field)
        if ref_price <= 0:
            return 0.0, ref_price

        order_type = cfg.order_type.lower()
        if order_type in {"market", "opponent"}:
            return 0.0, ref_price

        if order_type != "limit":
            raise ValueError(f"Unsupported order_type: {cfg.order_type}")

        if explicit_limit_price is not None:
            return float(explicit_limit_price), ref_price

        tick_size = float(account.fee_model._get_meta_data(symbol).get("tick_size", 1.0))
        ticks = float(cfg.ticks)
        is_buy_side = order_direction == Direction.LONG
        limit_mode = cfg.limit_mode.lower()

        if limit_mode in {"at_close", "at_price"}:
            limit_price = ref_price
        elif limit_mode == "better_ticks":
            limit_price = ref_price - ticks * tick_size if is_buy_side else ref_price + ticks * tick_size
        elif limit_mode == "worse_ticks":
            limit_price = ref_price + ticks * tick_size if is_buy_side else ref_price - ticks * tick_size
        else:
            raise ValueError(f"Unsupported limit_mode: {cfg.limit_mode}")

        return max(0.0, limit_price), ref_price

    @staticmethod
    def _reference_price(bar: dict, price_field: str) -> float:
        candidate_fields = [price_field]
        for field in ("mid_price", "last_price", "close", "open"):
            if field not in candidate_fields:
                candidate_fields.append(field)

        for field in candidate_fields:
            price = bar.get(field)
            if price is None or pd.isna(price):
                continue
            price = float(price)
            if price > 0:
                return price
        return 0.0
