# -*- coding: utf-8 -*-
"""
横截面多品种双均线轮动策略
继承自 PortfolioTemplate，引擎全自动处理资金分配与平反手操作。
"""
import pandas as pd
from strategy.base import PortfolioTemplate

class MultiMAStrategy(PortfolioTemplate):
    def __init__(self, broker, account, symbol, target_symbols, fast_window=10, slow_window=30, capital_pct=0.05):
        super().__init__(broker, account, symbols=target_symbols)

        self.fast_window = int(fast_window)
        self.slow_window = int(slow_window)
        self.capital_pct = float(capital_pct)
        self.history = {sym: [] for sym in self.symbols}

    def on_init(self):
        super().on_init()
        print(f"🚀 [全市场轮动] 启动！监控品种数: {len(self.symbols)} | 每个品种资金分配: {self.capital_pct * 100:.1f}%")

    # 💥 注意：这里彻底删除了错误多余的 on_bar 方法，全权交由底座的 PortfolioTemplate 接管！

    def generate_target_portfolio(self, bar_data: dict) -> dict:
        """
        核心逻辑：遍历全市场，输出每个品种的【目标净手数】
        """
        target_portfolio = {}

        for sym in self.symbols:
            if sym not in bar_data or pd.isna(bar_data[sym].get('close')):
                continue

            close_price = bar_data[sym]['close']
            self.history[sym].append(close_price)

            if len(self.history[sym]) > self.slow_window + 1:
                self.history[sym].pop(0)
            if len(self.history[sym]) < self.slow_window + 1:
                continue

            prices = self.history[sym]
            fast_ma_curr = sum(prices[-self.fast_window:]) / self.fast_window
            slow_ma_curr = sum(prices[-self.slow_window:]) / self.slow_window
            fast_ma_prev = sum(prices[-(self.fast_window + 1):-1]) / self.fast_window
            slow_ma_prev = sum(prices[-(self.slow_window + 1):-1]) / self.slow_window

            golden_cross = (fast_ma_prev <= slow_ma_prev) and (fast_ma_curr > slow_ma_curr)
            death_cross = (fast_ma_prev >= slow_ma_prev) and (fast_ma_curr < slow_ma_curr)

            target_margin = self.account.initial_capital * self.capital_pct
            meta = self.account.fee_model._get_meta_data(sym)
            margin_per_lot = close_price * meta['multiplier'] * meta['margin_rate']
            vol = int(target_margin // margin_per_lot) if margin_per_lot > 0 else 1
            vol = max(1, vol)

            if golden_cross:
                target_portfolio[sym] = vol
            elif death_cross:
                target_portfolio[sym] = -vol
            else:
                target_portfolio[sym] = self.get_net_position(sym)

        return target_portfolio