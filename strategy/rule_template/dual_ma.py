# -*- coding: utf-8 -*-
"""
双均线交叉策略 (继承自 RuleTemplate)
"""
from strategy.rule_template.rule import RuleTemplate


class DualMAStrategy(RuleStrategy):  # 或者你的其他基类名
    def __init__(self, broker, account, symbol, fast_window=10, slow_window=30, fixed_volume=None, capital_pct=None):
        # 如果你的基类接受 symbol，请这样传：
        # super().__init__(broker, account, symbol)

        # 如果基类不接受，或者你没写 super，请务必在这里显式绑定：
        super().__init__(broker, account)
        self.symbol = symbol  # 💥 就是漏了这一行！把传进来的参数存为自身属性

        self.fast_window = fast_window
        self.slow_window = slow_window
        self.fixed_volume = fixed_volume
        self.capital_pct = capital_pct

    def calculate_signal(self, bar: dict) -> int:
        """
        核心逻辑区：只负责输出目标状态 (1, -1, 或维持原状)
        """
        # 直接取用父类维护好的序列缓存
        fast_ma_curr = sum(self.close_prices[-self.fast_window:]) / self.fast_window
        slow_ma_curr = sum(self.close_prices[-self.slow_window:]) / self.slow_window

        fast_ma_prev = sum(self.close_prices[-(self.fast_window + 1):-1]) / self.fast_window
        slow_ma_prev = sum(self.close_prices[-(self.slow_window + 1):-1]) / self.slow_window

        # 判定逻辑
        golden_cross = (fast_ma_prev <= slow_ma_prev) and (fast_ma_curr > slow_ma_curr)
        death_cross = (fast_ma_prev >= slow_ma_prev) and (fast_ma_curr < slow_ma_curr)

        # 向上层状态机抛出目标动作
        if golden_cross:
            return 1
        elif death_cross:
            return -1

        # 既没金叉也没死叉，维持当前状态 (父类检测到无变化，不会有任何动作)
        return self.current_pos