# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SizingConfig:
    mode: str = "fixed_volume"
    value: float = 1.0
    min_volume: int = 1
    max_volume: int | None = None
    round_lot: int = 1


@dataclass
class ExecutionConfig:
    order_type: str = "market"
    limit_mode: str = "at_close"
    ticks: float = 0.0
    price_field: str = "close"
    slippage_ticks: float = 1.0


@dataclass
class ExitConfig:
    close_pct: float = 1.0
    allow_reverse: bool = True
    respect_pending_orders: bool = True


@dataclass
class SignalIntent:
    """Normalized strategy output consumed by SignalRebalancer."""

    direction: int | None
    position_mode: str | None = None
    size_scale: float | None = None
    target_volume: int | None = None
    target_net: int | None = None
    target_pct: float | None = None
    delta_volume: int | None = None
    delta_pct: float | None = None
    close_pct: float | None = None
    close_volume: int | None = None
    max_position_scale: float | None = None
    limit_price: float | None = None
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def _coerce_dataclass(config, cls):
    if config is None:
        return cls()
    if isinstance(config, cls):
        return config
    if isinstance(config, dict):
        return cls(**config)
    raise TypeError(f"{cls.__name__} config must be dict or {cls.__name__}")


def coerce_sizing_config(config) -> SizingConfig:
    return _coerce_dataclass(config, SizingConfig)


def coerce_execution_config(config) -> ExecutionConfig:
    return _coerce_dataclass(config, ExecutionConfig)


def coerce_exit_config(config) -> ExitConfig:
    return _coerce_dataclass(config, ExitConfig)


def normalize_signal(value) -> SignalIntent:
    if isinstance(value, SignalIntent):
        return value

    if value is None:
        return SignalIntent(direction=None)

    if isinstance(value, dict):
        known_keys = {
            "signal",
            "direction",
            "position_mode",
            "mode",
            "size_scale",
            "target_volume",
            "target_net",
            "target_pct",
            "delta_volume",
            "delta_pct",
            "close_pct",
            "close_volume",
            "max_position_scale",
            "limit_price",
            "reason",
            "metrics",
        }
        direction = value.get("signal", value.get("direction"))
        extra = {k: v for k, v in value.items() if k not in known_keys}
        return SignalIntent(
            direction=_normalize_direction(direction),
            position_mode=value.get("position_mode", value.get("mode")),
            size_scale=value.get("size_scale"),
            target_volume=value.get("target_volume"),
            target_net=value.get("target_net"),
            target_pct=value.get("target_pct"),
            delta_volume=value.get("delta_volume"),
            delta_pct=value.get("delta_pct"),
            close_pct=value.get("close_pct"),
            close_volume=value.get("close_volume"),
            max_position_scale=value.get("max_position_scale"),
            limit_price=value.get("limit_price"),
            reason=value.get("reason", ""),
            metrics=value.get("metrics") or {},
            extra=extra,
        )

    return SignalIntent(direction=_normalize_direction(value))


def _normalize_direction(value) -> int | None:
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"long", "buy", "1"}:
            return 1
        if text in {"short", "sell", "-1"}:
            return -1
        if text in {"flat", "exit", "close", "0"}:
            return 0
        if text in {"hold", "none", ""}:
            return None
        raise ValueError(f"Unknown signal direction: {value}")

    numeric = int(value)
    if numeric > 0:
        return 1
    if numeric < 0:
        return -1
    return 0
