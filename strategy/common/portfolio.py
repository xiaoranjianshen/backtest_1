# -*- coding: utf-8 -*-
"""
组合级目标信号辅助函数。

动态选品策略通常只想表达“今天选哪些品种、每个品种给多少权重”。
这个文件负责把选品权重转换成 GeneralSignalStrategy 能识别的标准信号，
并自动给被剔除但仍有持仓的品种发出全平信号。
"""

from __future__ import annotations


def build_target_margin_signals(
    strategy,
    selected_weights: dict[str, float],
    gross_margin_target: float = 0.25,
    reason: str = "selected_by_model",
    exit_reason: str = "removed_from_selected_pool",
) -> dict:
    """
    把选品权重转换成 target_margin_pct 信号。

    参数说明：
    - strategy：当前策略对象，必须继承 GeneralSignalStrategy。
    - selected_weights：选品结果。key 是品种，value 是权重；正数做多，负数做空。
    - gross_margin_target：组合目标保证金占当前权益比例，例如 0.25 表示总保证金约 25%。
    - reason：入选品种的信号原因。
    - exit_reason：被剔除品种的平仓原因。

    返回值：
    - 入选品种返回 target_margin_pct。
    - 未入选但当前有持仓的品种返回 flat。
    """
    gross_margin_target = max(0.0, float(gross_margin_target))
    clean_weights = {
        str(sym).lower(): float(weight)
        for sym, weight in (selected_weights or {}).items()
        if weight is not None and float(weight) != 0.0
    }
    total_abs_weight = sum(abs(weight) for weight in clean_weights.values())
    target_margin_by_symbol = {}
    if gross_margin_target > 0 and total_abs_weight > 0:
        target_margin_by_symbol = {
            sym: gross_margin_target * weight / total_abs_weight
            for sym, weight in clean_weights.items()
        }

    signals = {}
    for sym in strategy.symbols:
        sym_key = str(sym).lower()
        target_margin_pct = target_margin_by_symbol.get(sym_key, 0.0)
        if target_margin_pct != 0.0:
            signals[sym_key] = {
                "target_margin_pct": target_margin_pct,
                "reason": reason,
                "metrics": {
                    "selector_weight": clean_weights.get(sym_key, 0.0),
                    "target_margin_pct": target_margin_pct,
                    "gross_margin_target": gross_margin_target,
                },
            }
            continue

        if strategy.get_net_position(sym_key) != 0:
            signals[sym_key] = {
                "signal": 0,
                "position_mode": "flat",
                "reason": exit_reason,
            }

    return signals
