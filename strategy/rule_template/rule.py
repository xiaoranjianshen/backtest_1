# -*- coding: utf-8 -*-
"""
规则类策略模板 (RuleTemplate)
职责：封装序列缓存、目标仓位路由、支持【固定手数】与【资金比例】双模式开仓
"""
import pandas as pd
from datetime import datetime
from strategy.base import BaseStrategy


class RuleTemplate(BaseStrategy):
    def __init__(self, broker, account, symbol, warmup_bars: int = 50,
                 fixed_volume: int = None, capital_pct: float = None):
        super().__init__(broker, account, symbol)

        self.warmup_bars = warmup_bars

        # 💥 仓位管理双模开关
        # 如果使用者传了 fixed_volume，优先使用固定手数；否则使用资金比例
        self.fixed_volume = fixed_volume
        self.capital_pct = capital_pct

        if self.fixed_volume is None and self.capital_pct is None:
            raise ValueError("[规则模板] 必须提供 fixed_volume 或 capital_pct 其中之一！")

        self.close_prices = []
        self.current_pos = 0
        self.current_volume = 0

    def on_init(self):
        mode_desc = f"固定 {self.fixed_volume} 手" if self.fixed_volume else f"资金 {self.capital_pct * 100}%"
        print(f"[规则模板] 挂载标的: {self.symbol} | 预热周期: {self.warmup_bars} | 仓位模式: {mode_desc}")
        self.inited = True

    def get_target_volume(self, price: float) -> int:
        """核心路由：根据初始化配置，自动计算本次应该开多少手"""
        # 模式 A: 固定手数
        if self.fixed_volume is not None:
            return self.fixed_volume

        # 模式 B: 按初始资金比例计算
        if self.capital_pct is not None:
            target_margin = self.account.initial_capital * self.capital_pct
            meta = self.account.fee_model._get_meta_data(self.symbol)
            margin_per_lot = price * meta['multiplier'] * meta['margin_rate']

            volume = int(target_margin // margin_per_lot)
            return volume if volume > 0 else 1

    def on_bar(self, current_time: datetime, bar_data: dict):
        if self.symbol not in bar_data or pd.isna(bar_data[self.symbol]['close']):
            return

        current_close = bar_data[self.symbol]['close']

        self.close_prices.append(current_close)
        if len(self.close_prices) > self.warmup_bars + 1:
            self.close_prices.pop(0)

        if len(self.close_prices) < self.warmup_bars + 1:
            return

        target_pos = self.calculate_signal(bar_data[self.symbol])
        self._route_target_position(target_pos, current_close, current_time)

    def calculate_signal(self, bar: dict) -> int:
        raise NotImplementedError("子类必须实现 calculate_signal 方法！")

    def _route_target_position(self, target_pos: int, current_price: float, current_time: datetime):
        if target_pos == self.current_pos:
            return

        # 1. 平仓动作 (全平)
        if self.current_pos == 1:
            print(f"[{current_time}] [执行] 多单平仓 {self.current_volume} 手")
            self.sell(volume=self.current_volume, price=0.0, reference_price=current_price)
            self.current_pos = 0
            self.current_volume = 0

        elif self.current_pos == -1:
            print(f"[{current_time}] [执行] 空单平仓 {self.current_volume} 手")
            self.cover(volume=self.current_volume, price=0.0, reference_price=current_price)
            self.current_pos = 0
            self.current_volume = 0

        # 2. 开仓动作 (调用双模计算器获取手数)
        if target_pos == 1:
            dynamic_vol = self.get_target_volume(current_price)
            print(f"[{current_time}] [执行] 开多 {dynamic_vol} 手")
            self.buy(volume=dynamic_vol, price=0.0, reference_price=current_price)
            self.current_pos = 1
            self.current_volume = dynamic_vol

        elif target_pos == -1:
            dynamic_vol = self.get_target_volume(current_price)
            print(f"[{current_time}] [执行] 开空 {dynamic_vol} 手")
            self.short(volume=dynamic_vol, price=0.0, reference_price=current_price)
            self.current_pos = -1
            self.current_volume = dynamic_vol