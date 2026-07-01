# -*- coding: utf-8 -*-
"""
通用策略协议说明。

这个文件定义“策略能对回测引擎说什么”。普通策略不需要直接下单，
只需要在 generate_signals() 里返回这些标准字段，后续由调仓器、
仓位计算器和撮合引擎统一处理。

最常见的信号格式：

    {
        "rb": {
            "signal": 1,
            "position_mode": "target",
            "reason": "golden_cross",
            "metrics": {"fast_ma": 4100, "slow_ma": 4050},
        }
    }

字段含义见下面各个 dataclass 的中文注释。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SizingConfig:
    """
    仓位计算配置。

    这些 mode 不一定每个策略都会用到，但都是当前框架已经支持的功能：

    - fixed_volume：固定手数。value=2 表示每次基础仓位为 2 手。
    - fixed_margin：固定保证金金额。value=100000 表示按 10 万保证金反推手数。
    - fixed_notional：固定名义市值。value=1000000 表示按 100 万合约市值反推手数。
    - capital_pct：按初始资金比例。value=0.03 表示用初始资金的 3% 作为保证金。
    - equity_pct：按当前总权益比例。value=0.03 表示用当前权益的 3% 作为保证金。
    - available_pct：按当前可用资金比例。value=0.03 表示用当前可用资金的 3% 作为保证金。

    注意：这是“基础仓位”的计算方式。策略信号还可以通过 target_volume、
    target_net、target_weight、target_margin_pct、risk_pct 等字段覆盖或细化仓位。
    """

    # 仓位计算模式。默认 fixed_volume，适合最小样例和 smoke test。
    mode: str = "fixed_volume"
    # 模式参数。不同 mode 下含义不同，例如固定手数、金额或比例。
    value: float = 1.0
    # 最小下单手数。设为 1 时，只要理论手数大于 0 但小于 1，也会下 1 手。
    min_volume: int = 1
    # 最大下单手数。None 表示不限制。
    max_volume: int | None = None
    # 手数取整单位。国内期货通常为 1；如有特殊合约可改成更大单位。
    round_lot: int = 1


@dataclass
class ExecutionConfig:
    """
    执行方式配置。

    支持的 order_type：

    - market：市价单。K线回测里用下一根 bar 的可成交价格撮合，会应用 slippage_ticks。
    - limit：限价单。按 limit_mode 生成挂单价格，后续由行情高低点或 tick 触发成交。
    - opponent：对价单。主要用于 tick 回测，买单吃卖一、卖单吃买一，不额外叠加滑点。

    支持的 limit_mode：

    - at_close / at_price：按参考价挂限价单。
    - better_ticks：比参考价更优 ticks 跳，买单更低、卖单更高。
    - worse_ticks：比参考价更差 ticks 跳，买单更高、卖单更低，更容易成交。
    """

    # 订单类型：market、limit、opponent。
    order_type: str = "market"
    # 限价单定价方式。只有 order_type="limit" 时生效。
    limit_mode: str = "at_close"
    # 限价偏移跳数。配合 better_ticks / worse_ticks 使用。
    ticks: float = 0.0
    # 参考价格字段。日线/分钟线通常用 close，tick 可用 last_price 或 mid_price。
    price_field: str = "close"
    # 市价单滑点跳数。限价单本身不额外叠加成交滑点。
    slippage_ticks: float = 1.0
    # 订单有效期秒数。None 表示不按时间自动撤单。
    order_ttl_seconds: float | None = None


@dataclass
class ExitConfig:
    """
    平仓和反手规则。

    这些配置是全局兜底规则；策略也可以在单个信号里用 close_pct 或 close_volume
    指定本次平仓比例/手数。
    """

    # signal=0 且未单独指定 close_pct/close_volume 时，默认平仓比例。
    close_pct: float = 1.0
    # 是否允许信号直接从多头反到空头，或从空头反到多头。
    allow_reverse: bool = True
    # 是否把未成交挂单也计入目标仓位差额，避免重复下同方向订单。
    respect_pending_orders: bool = True


@dataclass
class SignalIntent:
    """
    标准化后的策略信号。

    策略作者可以直接返回 dict，不一定要手动创建 SignalIntent。
    normalize_signal() 会把 dict 转成这个结构。
    """

    # 方向：1 做多，-1 做空，0 平仓/减仓，None 表示没有交易动作。
    direction: int | None
    # 仓位模式：target 目标仓位，delta 增减仓，reduce 部分平仓，flat 全平。
    position_mode: str | None = None
    # 基础仓位缩放。例如基础仓位 10 手，size_scale=0.5 则目标 5 手。
    size_scale: float | None = None
    # 信号分数。只用于信号检测、IC/Rank IC，不直接决定下单方向或手数。
    signal_score: float | None = None
    # 指定目标手数，不带方向；方向由 signal 决定。
    target_volume: int | None = None
    # 指定目标净持仓。正数为多头，负数为空头，0 为空仓。
    target_net: int | None = None
    # 对基础仓位做百分比缩放。和 size_scale 类似，优先级高于 size_scale。
    target_pct: float | None = None
    # 目标名义市值占当前权益比例。可为正负；正为多，负为空。
    target_weight: float | None = None
    # 目标保证金占当前权益比例。可为正负；适合动态选品和组合分配。
    target_margin_pct: float | None = None
    # 指定增减手数。配合 position_mode="delta" 使用。
    delta_volume: int | None = None
    # 按基础仓位比例增减仓。配合 position_mode="delta" 使用。
    delta_pct: float | None = None
    # 平仓比例。例如 0.5 表示平当前持仓的一半。
    close_pct: float | None = None
    # 指定平仓手数。
    close_volume: int | None = None
    # 单笔风险占当前权益比例。需要配合 stop_loss_ticks 或 stop_loss_price。
    risk_pct: float | None = None
    # 按止损跳数反推 risk_pct 对应的下单手数。
    stop_loss_ticks: float | None = None
    # 按止损价格反推 risk_pct 对应的下单手数。
    stop_loss_price: float | None = None
    # 最大仓位缩放上限。用于限制加仓后的最大绝对仓位。
    max_position_scale: float | None = None
    # 单个信号指定限价价格。优先级高于 execution.limit_mode 自动计算价格。
    limit_price: float | None = None
    # 信号原因。会进入复盘、信号检测和 CSV。
    reason: str = ""
    # 策略指标。建议放入 close、均线、zscore、模型分数等可解释字段。
    metrics: dict[str, Any] = field(default_factory=dict)
    # 未被协议识别的额外字段。高级用法，例如单个信号覆盖 order_type。
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
    """把策略返回的原始值统一转换成 SignalIntent。"""
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
            "signal_score",
            "target_volume",
            "target_net",
            "target_pct",
            "target_weight",
            "target_margin_pct",
            "delta_volume",
            "delta_pct",
            "close_pct",
            "close_volume",
            "risk_pct",
            "stop_loss_ticks",
            "stop_loss_price",
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
            signal_score=value.get("signal_score"),
            target_volume=value.get("target_volume"),
            target_net=value.get("target_net"),
            target_pct=value.get("target_pct"),
            target_weight=value.get("target_weight"),
            target_margin_pct=value.get("target_margin_pct"),
            delta_volume=value.get("delta_volume"),
            delta_pct=value.get("delta_pct"),
            close_pct=value.get("close_pct"),
            close_volume=value.get("close_volume"),
            risk_pct=value.get("risk_pct"),
            stop_loss_ticks=value.get("stop_loss_ticks"),
            stop_loss_price=value.get("stop_loss_price"),
            max_position_scale=value.get("max_position_scale"),
            limit_price=value.get("limit_price"),
            reason=value.get("reason", ""),
            metrics=value.get("metrics") or {},
            extra=extra,
        )

    return SignalIntent(direction=_normalize_direction(value))


def _normalize_direction(value) -> int | None:
    """把 long/short/flat/hold 等写法统一转成 1/-1/0/None。"""
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
