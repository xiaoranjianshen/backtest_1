# -*- coding: utf-8 -*-
"""
截面动量反转策略 (继承自 FactorTemplate)
逻辑：计算截面所有品种的日内涨跌幅，做多跌幅最大的，做空涨幅最大的。
"""
from strategy.factor_template.factor import FactorTemplate


class CrossMomentumFactor(FactorTemplate):
    def __init__(
        self,
        broker,
        account,
        symbol='multi',
        target_symbols=None,
        rebalance_period=1,
        top_k=1,
        signal_scale=1.0,
        **kwargs,
    ):
        super().__init__(
            broker,
            account,
            symbol,
            target_symbols=target_symbols,
            rebalance_period=rebalance_period,
            **kwargs,
        )

        self.top_k = top_k  # 选取截面前 k 个和后 k 个品种
        self.signal_scale = float(signal_scale)

    def calculate_weights(self, cross_section: dict) -> dict:
        """
        核心因子计算区：接收全市场截面字典，返回目标权重字典
        """
        factors = {}

        # 1. 计算截面因子值 (例如: 简单日内涨跌幅 = close / open - 1)
        for sym, bar in cross_section.items():
            if bar['open'] == 0:
                continue
            momentum = bar['close'] / bar['open'] - 1.0
            factors[sym] = momentum

        if not factors:
            return {}

        # 2. 截面排序 (升序：跌得最惨的排前面，涨得最好的排后面)
        sorted_syms = sorted(factors.keys(), key=lambda s: factors[s])

        target_weights = {}

        # 3. 分配权重
        # 动量反转逻辑：买入跌得最多的 (排在前面的)，卖出涨得最好的 (排在后面的)
        # 确保截面品种数量足够支撑我们选出 top_k
        actual_k = min(self.top_k, len(sorted_syms) // 2)
        if actual_k == 0:
            return {}

        # 做多跌得最惨的
        for sym in sorted_syms[:actual_k]:
            target_weights[sym] = self.signal_scale  # 正值代表做多

        # 做空涨得最好的
        for sym in sorted_syms[-actual_k:]:
            target_weights[sym] = -self.signal_scale  # 负值代表做空

        return target_weights
