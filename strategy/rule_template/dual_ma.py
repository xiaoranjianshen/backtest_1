# -*- coding: utf-8 -*-
"""
双均线交叉策略 (继承自 RuleTemplate)
"""
from strategy.rule_template.rule import RuleTemplate


class DualMAStrategy(RuleTemplate):
    # 💥 把 fixed_volume 和 capital_pct 作为可选参数暴露出来
    def __init__(self, broker, account, symbol, fast_window=10, slow_window=30,
                 fixed_volume=None, capital_pct=None):

        # 抛给父类去处理仓位路由
        super().__init__(broker, account, symbol, warmup_bars=slow_window,
                         fixed_volume=fixed_volume, capital_pct=capital_pct)

        self.fast_window = fast_window
        self.slow_window = slow_window

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