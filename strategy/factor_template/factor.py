# -*- coding: utf-8 -*-
"""
多因子截面策略模板 (FactorTemplate)
职责：提取横截面数据、获取子类目标权重矩阵、执行多品种自动调仓对齐
"""
import pandas as pd
from datetime import datetime
from strategy.base import BaseStrategy
from broker.order import Order, Direction, Offset, OrderType


class FactorTemplate(BaseStrategy):
    def __init__(self, broker, account, symbol='multi', rebalance_period: int = 1):
        """
        :param rebalance_period: 调仓周期 (默认 1 根 K 线调仓一次)
        """
        # 多因子通常针对全市场，symbol 只是个占位符
        super().__init__(broker, account, symbol)

        self.rebalance_period = rebalance_period
        self.bar_count = 0

        # 维护当前全市场各品种的真实持仓手数 {'rb': 10, 'i': -5}
        self.current_volumes = {}

    def on_init(self):
        print(f"[多因子模板] 初始化完成 | 调仓频率: 每 {self.rebalance_period} 周期")
        self.inited = True

    def _send_cross_order(self, sym: str, direction: Direction, offset: Offset, volume: int):
        """多因子专属发单通道：允许向非 self.symbol 的特定品种发送指令"""
        order = Order(
            symbol=sym, direction=direction, offset=offset,
            volume=volume, price=0.0, order_type=OrderType.MARKET, insert_time=datetime.now()
        )
        self.broker.insert_order(order, reference_price=0.0)

    def on_bar(self, current_time: datetime, bar_data: dict):
        """核心驱动：每到达调仓周期，提取横截面数据并执行调仓"""
        self.bar_count += 1

        # 1. 周期控制：未到调仓日，直接放行
        if self.bar_count % self.rebalance_period != 0:
            return

        # 2. 提取有效横截面数据 (剔除停牌或无数据的品种)
        cross_section = {
            sym: data for sym, data in bar_data.items()
            if data and not pd.isna(data.get('close'))
        }

        if not cross_section:
            return

        # 3. 呼叫子类：传入横截面，获取目标权重向量
        # 期望返回格式: {'rb': 0.10, 'i': -0.15, 'ta': 0.05}
        target_weights = self.calculate_weights(cross_section)

        # 4. 执行截面调仓
        self._rebalance_portfolio(target_weights, cross_section, current_time)

    def calculate_weights(self, cross_section: dict) -> dict:
        """纯虚函数：留给具体的因子逻辑去实现截面打分与权重分配"""
        raise NotImplementedError("子类必须实现 calculate_weights 方法！")

    def _rebalance_portfolio(self, target_weights: dict, cross_section: dict, current_time: datetime):
        """核心路由：计算目标手数差额，生成一揽子调仓指令"""
        target_volumes = {}

        # A. 计算目标绝对手数
        for sym, weight in target_weights.items():
            if weight == 0 or sym not in cross_section:
                continue

            price = cross_section[sym]['close']
            meta = self.account.fee_model._get_meta_data(sym)
            margin_per_lot = price * meta['multiplier'] * meta['margin_rate']

            # 使用初始资金作为静态计算基准 (也可改为动态权益)
            target_margin = self.account.initial_capital * abs(weight)
            vol = int(target_margin // margin_per_lot)

            if vol > 0:
                target_volumes[sym] = vol if weight > 0 else -vol

        # B. 调仓第一阶段：平仓与减仓 (必须先释放保证金)
        for sym in list(self.current_volumes.keys()):
            curr_v = self.current_volumes.get(sym, 0)
            targ_v = target_volumes.get(sym, 0)

            if curr_v == 0 or sym not in cross_section:
                continue

            # 多头减仓或反向
            if curr_v > 0 and targ_v < curr_v:
                close_vol = curr_v - max(targ_v, 0)
                print(f"[{current_time}] [调仓] {sym} 平多 {close_vol} 手")
                self._send_cross_order(sym, Direction.SHORT, Offset.CLOSE, close_vol)
                self.current_volumes[sym] -= close_vol

            # 空头减仓或反向
            elif curr_v < 0 and targ_v > curr_v:
                close_vol = abs(curr_v) - abs(min(targ_v, 0))
                print(f"[{current_time}] [调仓] {sym} 平空 {close_vol} 手")
                self._send_cross_order(sym, Direction.LONG, Offset.CLOSE, close_vol)
                self.current_volumes[sym] += close_vol

        # C. 调仓第二阶段：开仓与加仓
        for sym, targ_v in target_volumes.items():
            curr_v = self.current_volumes.get(sym, 0)

            # 多头加仓
            if targ_v > 0 and targ_v > curr_v:
                open_vol = targ_v - max(curr_v, 0)
                print(f"[{current_time}] [调仓] {sym} 开多 {open_vol} 手 (权重: {target_weights[sym] * 100:.1f}%)")
                self._send_cross_order(sym, Direction.LONG, Offset.OPEN, open_vol)
                self.current_volumes[sym] = targ_v

            # 空头加仓
            elif targ_v < 0 and targ_v < curr_v:
                open_vol = abs(targ_v) - abs(min(curr_v, 0))
                print(f"[{current_time}] [调仓] {sym} 开空 {open_vol} 手 (权重: {target_weights[sym] * 100:.1f}%)")
                self._send_cross_order(sym, Direction.SHORT, Offset.OPEN, open_vol)
                self.current_volumes[sym] = targ_v