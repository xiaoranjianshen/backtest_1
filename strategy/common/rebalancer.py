# -*- coding: utf-8 -*-
import math

import pandas as pd

from broker.order import Direction, OrderType
from config import pure_product_code
from strategy.common.execution import ExecutionPolicy
from strategy.common.sizing import PositionSizer
from strategy.common.types import ExitConfig, coerce_exit_config, normalize_signal


class SignalRebalancer:
    """Convert standard strategy intents into executable order requests."""

    def __init__(self, strategy, sizing=None, execution=None, exit_config=None):
        self.strategy = strategy
        self.sizer = PositionSizer(sizing)
        self.execution = ExecutionPolicy(execution)
        self.exit_config: ExitConfig = coerce_exit_config(exit_config)

    def rebalance(self, signals: dict, bar_data: dict) -> list[dict]:
        records = []
        current_prices = self._current_prices(bar_data)

        for sym, raw_signal in (signals or {}).items():
            if sym not in bar_data or pd.isna(bar_data[sym].get("close", pd.NA)):
                continue

            intent = normalize_signal(raw_signal)
            current_net = self.strategy.get_net_position(sym)
            working_net = current_net
            if self.exit_config.respect_pending_orders:
                working_net += self._pending_net_delta(sym)

            record = {
                "symbol": sym,
                "signal": intent.direction,
                "position_mode": intent.position_mode,
                "reason": intent.reason,
                "current_net": current_net,
                "working_net": working_net,
                "metrics": intent.metrics,
                **intent.extra,
            }

            if intent.direction is None and intent.target_net is None and intent.position_mode is None:
                record.update({"target_net": working_net, "diff": 0, "action": "hold"})
                records.append(record)
                continue

            target_net = self._resolve_target_net(sym, intent, bar_data[sym], current_prices, working_net)
            working_net, canceled_count = self._cancel_pending_towards_target(sym, working_net, target_net)
            diff = target_net - working_net
            record.update({
                "target_net": target_net,
                "working_net_after_cancel": working_net,
                "canceled_pending_orders": canceled_count,
                "diff": diff,
            })

            if diff == 0:
                record["action"] = "no_order"
                records.append(record)
                continue

            self._submit_diff(sym, diff, bar_data[sym], intent)
            record["action"] = "order_submitted"
            records.append(record)

        return records

    def _resolve_target_net(self, sym: str, intent, bar: dict, current_prices: dict, current_net: int) -> int:
        mode = self._normalize_position_mode(intent.position_mode, intent.direction)
        if intent.target_net is not None:
            return self._apply_reverse_rule(current_net, int(intent.target_net))

        if mode == "flat":
            return 0

        direction = intent.direction
        if direction == 0 or mode == "reduce":
            return self._target_after_close(current_net, intent)

        if direction is None:
            return current_net

        base_volume = self.sizer.calculate(sym, bar["close"], self.strategy.account, current_prices)
        max_abs_volume = self._max_abs_volume(base_volume, intent)

        if mode == "delta":
            delta_volume = self._delta_volume(base_volume, intent)
            target_net = current_net + int(direction) * delta_volume
            target_net = self._cap_abs_target(target_net, max_abs_volume)
            return self._apply_reverse_rule(current_net, target_net)

        target_volume = self._target_volume(base_volume, intent)
        target_net = int(direction) * target_volume
        target_net = self._cap_abs_target(target_net, max_abs_volume)
        return self._apply_reverse_rule(current_net, target_net)

    def _target_after_close(self, current_net: int, intent) -> int:
        if current_net == 0:
            return 0

        close_volume = intent.close_volume
        if close_volume is None:
            close_pct = self._bounded_pct(intent.close_pct if intent.close_pct is not None else self.exit_config.close_pct)
            close_volume = int(math.floor(abs(current_net) * close_pct))
            if close_pct > 0 and close_volume == 0:
                close_volume = 1

        close_volume = max(0, min(abs(current_net), int(close_volume)))
        if current_net > 0:
            return current_net - close_volume
        return current_net + close_volume

    def _target_volume(self, base_volume: int, intent) -> int:
        if intent.target_volume is not None:
            return max(0, int(intent.target_volume))

        scale = intent.target_pct if intent.target_pct is not None else intent.size_scale
        if scale is None:
            return max(0, int(base_volume))

        return self._scale_volume(base_volume, float(scale))

    def _delta_volume(self, base_volume: int, intent) -> int:
        if intent.delta_volume is not None:
            return max(0, int(intent.delta_volume))

        scale = intent.delta_pct if intent.delta_pct is not None else intent.size_scale
        if scale is None:
            return max(0, int(base_volume))

        return self._scale_volume(base_volume, float(scale))

    def _scale_volume(self, base_volume: int, scale: float) -> int:
        if base_volume <= 0 or scale <= 0:
            return 0

        raw_volume = float(base_volume) * scale
        volume = int(math.floor(raw_volume))
        if raw_volume > 0 and volume < self.sizer.config.min_volume:
            volume = int(self.sizer.config.min_volume)
        return max(0, volume)

    def _max_abs_volume(self, base_volume: int, intent) -> int | None:
        if intent.max_position_scale is None:
            return None
        return self._scale_volume(base_volume, float(intent.max_position_scale))

    @staticmethod
    def _cap_abs_target(target_net: int, max_abs_volume: int | None) -> int:
        if max_abs_volume is None:
            return target_net
        if target_net > max_abs_volume:
            return max_abs_volume
        if target_net < -max_abs_volume:
            return -max_abs_volume
        return target_net

    def _apply_reverse_rule(self, current_net: int, target_net: int) -> int:
        if not self.exit_config.allow_reverse and current_net * target_net < 0:
            return 0
        return target_net

    @staticmethod
    def _normalize_position_mode(mode: str | None, direction: int | None) -> str:
        if mode is None:
            return "reduce" if direction == 0 else "target"
        normalized = str(mode).strip().lower()
        aliases = {
            "exit": "reduce",
            "close": "reduce",
            "partial_close": "reduce",
            "all_flat": "flat",
            "target_position": "target",
            "add": "delta",
            "increment": "delta",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"target", "delta", "flat", "reduce"}:
            raise ValueError(f"Unsupported position_mode: {mode}")
        return normalized

    def _submit_diff(self, sym: str, diff: int, bar: dict, intent):
        if diff > 0:
            short_vol = self.strategy.get_position_volume(sym, Direction.SHORT)
            cover_vol = min(diff, short_vol)
            if cover_vol > 0:
                self._send(sym, Direction.LONG, "cover", cover_vol, bar, intent)
                diff -= cover_vol
            if diff > 0:
                self._send(sym, Direction.LONG, "buy", diff, bar, intent)
            return

        sell_target = abs(diff)
        long_vol = self.strategy.get_position_volume(sym, Direction.LONG)
        sell_vol = min(sell_target, long_vol)
        if sell_vol > 0:
            self._send(sym, Direction.SHORT, "sell", sell_vol, bar, intent)
            sell_target -= sell_vol
        if sell_target > 0:
            self._send(sym, Direction.SHORT, "short", sell_target, bar, intent)

    def _send(self, sym: str, order_direction: Direction, action: str, volume: int, bar: dict, intent):
        order_type_override = str(intent.extra.get("order_type", "")).strip().lower()
        ttl_seconds = intent.extra.get("order_ttl_seconds", self.execution.config.order_ttl_seconds)

        if order_type_override in {"market", "opponent"}:
            price = 0.0
            reference_price = self.execution._reference_price(bar, self.execution.config.price_field)
            broker_order_type = OrderType.OPPONENT if order_type_override == "opponent" else OrderType.MARKET
            slippage_ticks = None if broker_order_type == OrderType.OPPONENT else self.execution.config.slippage_ticks
        else:
            price, reference_price = self.execution.get_order_prices(
                sym,
                order_direction,
                bar,
                self.strategy.account,
                explicit_limit_price=intent.limit_price,
            )
            configured_order_type = self.execution.config.order_type.lower()
            broker_order_type = OrderType.OPPONENT if configured_order_type == "opponent" else None
            slippage_ticks = self.execution.config.slippage_ticks if price == 0.0 and broker_order_type is None else None

        if action == "buy":
            self.strategy.buy(
                sym, volume=volume, price=price, reference_price=reference_price,
                slippage_ticks=slippage_ticks, order_type=broker_order_type, ttl_seconds=ttl_seconds
            )
        elif action == "sell":
            self.strategy.sell(
                sym, volume=volume, price=price, reference_price=reference_price,
                slippage_ticks=slippage_ticks, order_type=broker_order_type, ttl_seconds=ttl_seconds
            )
        elif action == "short":
            self.strategy.short(
                sym, volume=volume, price=price, reference_price=reference_price,
                slippage_ticks=slippage_ticks, order_type=broker_order_type, ttl_seconds=ttl_seconds
            )
        elif action == "cover":
            self.strategy.cover(
                sym, volume=volume, price=price, reference_price=reference_price,
                slippage_ticks=slippage_ticks, order_type=broker_order_type, ttl_seconds=ttl_seconds
            )
        else:
            raise ValueError(f"Unsupported rebalance action: {action}")

    def _pending_net_delta(self, symbol: str) -> int:
        raw_symbol = pure_product_code(symbol)
        pending_delta = 0
        for order in self.strategy.broker.pending_orders:
            if pure_product_code(order.symbol) != raw_symbol:
                continue
            pending_delta += self._order_net_delta(order)
        return pending_delta

    def _cancel_pending_towards_target(self, symbol: str, working_net: int, target_net: int) -> tuple[int, int]:
        raw_symbol = pure_product_code(symbol)
        canceled_count = 0

        for order in list(self.strategy.broker.pending_orders):
            if working_net == target_net:
                break
            if pure_product_code(order.symbol) != raw_symbol:
                continue

            delta = self._order_net_delta(order)
            should_cancel = (working_net > target_net and delta > 0) or (working_net < target_net and delta < 0)
            if not should_cancel:
                continue

            if self.strategy.broker.cancel_order(order.order_id):
                working_net -= delta
                canceled_count += 1

        return working_net, canceled_count

    @staticmethod
    def _order_net_delta(order) -> int:
        return order.volume if order.direction == Direction.LONG else -order.volume

    @staticmethod
    def _current_prices(bar_data: dict) -> dict:
        return {
            sym: data["close"]
            for sym, data in bar_data.items()
            if data and not pd.isna(data.get("close", pd.NA))
        }

    @staticmethod
    def _bounded_pct(value: float) -> float:
        return min(1.0, max(0.0, float(value)))
