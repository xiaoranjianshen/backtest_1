# -*- coding: utf-8 -*-
"""
双均线交叉策略 (继承自 RuleTemplate)
"""
from strategy.rule_template.rule import RuleTemplate


class DualMAStrategy(RuleTemplate):
    def __init__(self, broker, account, symbol, fast_window=10, slow_window=30, fixed_volume=None, capital_pct=None):
        self.fast_window = int(fast_window)
        self.slow_window = int(slow_window)
        if self.fast_window <= 0 or self.slow_window <= 0:
            raise ValueError("fast_window 和 slow_window 必须为正整数")
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window 必须小于 slow_window")

        super().__init__(
            broker=broker,
            account=account,
            symbol=symbol,
            warmup_bars=self.slow_window,
            fixed_volume=fixed_volume,
            capital_pct=capital_pct,
        )
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
