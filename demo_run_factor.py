# -*- coding: utf-8 -*-
"""
多因子回测任务控制台
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from backtest_engine import run_backtest
from strategy.factor_template.composite_factor import CompositeFactorStrategy

# =========================================================================
# ⚙️ 1. 回测参数配置区
# =========================================================================
# 定义截面品种池 (引擎会在底层自动将它们翻译为全拼)
TARGET_SYMBOLS = ['rb', 'hc', 'i', 'jm', 'j', 'ta', 'ma', 'fg', 'sa']

FREQ = '1d'
DATA_TYPE = 'main'
START_DATE = '2020-05-20 00:00:00'
END_DATE = '2026-05-20 23:59:59'
INITIAL_CAPITAL = 1000000.0

STRATEGY_CLASS = CompositeFactorStrategy

# 策略参数：每 5 天调仓一次，做多前 2 名，做空后 2 名，每个标的分配 10% 资金
STRATEGY_KWARGS = {'rebalance_period': 5, 'top_k': 2, 'weight_per_leg': 0.10}

# =========================================================================
# 🛠️ 2. 任务启动区
# =========================================================================
if __name__ == "__main__":
    print("[Runner] 正在初始化多因子截面回测任务...")

    # 💥 直接传入极简参数，脏活全部交给引擎
    run_backtest(
        strategy_class=STRATEGY_CLASS,
        symbols_input=TARGET_SYMBOLS,
        start_date=START_DATE,
        end_date=END_DATE,
        freq=FREQ,
        data_type=DATA_TYPE,
        initial_capital=INITIAL_CAPITAL,
        strategy_kwargs=STRATEGY_KWARGS
    )