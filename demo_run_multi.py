# -*- coding: utf-8 -*-
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from backtest_engine import run_backtest
from strategy.rule_template.multi_ma import MultiMAStrategy

# =========================================================================
# 💥 一次性跑 8 个品种！横跨黑色、化工、油脂、软商品
# =========================================================================
TARGET_SYMBOLS = ['rb', 'hc', 'i', 'ta', 'ma', 'p', 'y', 'sr'] 

# ... 保持你文件前面的部分不变 ...

if __name__ == "__main__":
    print("[Runner] 正在初始化多品种截面轮动回测...")

    # 1. 引擎算账，接住返回的 analyzer
    analyzer = run_backtest(
        strategy_class=MultiMAStrategy,
        symbols_input=TARGET_SYMBOLS,
        start_date='2020-01-01 00:00:00',
        end_date='2026-05-20 23:59:59',
        freq='1d',
        data_type='main',
        initial_capital=5000000.0,
        strategy_kwargs={
            'target_symbols': TARGET_SYMBOLS,
            'fast_window': 10,
            'slow_window': 30,
            'capital_pct': 0.10
        }
    )

    # 2. 调用前端工厂，自动弹窗！
    from frontend_index import build_html_dashboard
    build_html_dashboard(analyzer)