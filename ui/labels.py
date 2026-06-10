# -*- coding: utf-8 -*-


FREQ_LABELS = {
    "1d": "日线 (1d)",
    "5m": "5分钟 (5m)",
    "1m": "1分钟 (1m)",
    "tick": "Tick",
}

DATA_TYPE_LABELS = {
    "main": "主力连续未复权 (main)",
    "main_adj": "主力连续复权 (main_adj)",
    "index": "指数连续 (index)",
    "all": "全部合约明细 (all)",
}

SIZING_LABELS = {
    "fixed_volume": "固定手数 (Fixed Volume)",
    "fixed_margin": "固定保证金 (Fixed Margin)",
    "fixed_notional": "固定名义金额 (Fixed Notional)",
    "capital_pct": "初始资金比例 (Initial Capital %)",
    "equity_pct": "当前权益比例 (Current Equity %)",
    "available_pct": "可用资金比例 (Available Cash %)",
}

ORDER_TYPE_LABELS = {
    "market": "市价单 (Market)",
    "limit": "限价单 (Limit)",
}

LIMIT_MODE_LABELS = {
    "at_close": "当前收盘价 (At Close)",
    "better_ticks": "更优 N 跳 (Better Ticks)",
    "worse_ticks": "更差 N 跳 (Worse Ticks)",
}


def format_with_mapping(mapping):
    return lambda value: mapping.get(value, value)
