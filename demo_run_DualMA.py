# -*- coding: utf-8 -*-
"""
回测任务控制台 (Backtest Task Runner)
作用：在这里配置所有的回测参数，引擎会自动处理前缀路由
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# 导入底层引擎和策略库
from backtest_engine import run_backtest
from strategy.rule_template.dual_ma import DualMAStrategy

# =========================================================================
# ⚙️ 1. 回测参数配置区 (极简版)
# =========================================================================
TARGET_SYMBOL = 'rb'  # 极简品种代码，引擎会自动补全 (如 'rb', 'i', 'ta605')
FREQ = '1d'  # 数据频率: '1m', '5m', '1d', 'tick'
DATA_TYPE = 'main'  # 数据形态: 'main_adj', 'main', 'all', 'index'
START_DATE = '2020-05-20 00:00:00'
END_DATE = '2026-05-20 23:59:59'
INITIAL_CAPITAL = 1000000.0  # 初始回测资金

# 指定要运行的策略类
STRATEGY_CLASS = DualMAStrategy

# 策略参数与仓位管理双模切换 (必须提供 fixed_volume 或 capital_pct 其一)
# 模式 A: 固定手数
# STRATEGY_KWARGS = {'fast_window': 10, 'slow_window': 30, 'fixed_volume': 10}

# 模式 B: 动态资金比例
STRATEGY_KWARGS = {'fast_window': 10, 'slow_window': 30, 'capital_pct': 0.10}

# =========================================================================
# 🛠️ 2. 任务启动区
# =========================================================================
if __name__ == "__main__":
    print("[Runner] 正在初始化单品种规则回测任务...")


    run_backtest(
        strategy_class=STRATEGY_CLASS,
        symbols_input=TARGET_SYMBOL,
        start_date=START_DATE,
        end_date=END_DATE,
        freq=FREQ,
        data_type=DATA_TYPE,
        initial_capital=INITIAL_CAPITAL,
        strategy_kwargs=STRATEGY_KWARGS
    )