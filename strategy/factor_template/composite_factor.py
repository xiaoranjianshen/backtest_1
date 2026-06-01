# -*- coding: utf-8 -*-
"""
复合多因子截面策略 (继承自 FactorTemplate)
逻辑：合成 动量因子 (Momentum) 与 波动率因子 (Volatility) 的截面排名得分
"""
import pandas as pd
from strategy.factor_template.factor import FactorTemplate


class CompositeFactorStrategy(FactorTemplate):
    def __init__(self, broker, account, symbol='multi', rebalance_period=5, top_k=2, weight_per_leg=0.10):
        super().__init__(broker, account, symbol, rebalance_period=rebalance_period)

        self.top_k = top_k
        self.weight_per_leg = weight_per_leg

    def calculate_weights(self, cross_section: dict) -> dict:
        # 1. 构建截面数据集
        data_list = []
        for sym, bar in cross_section.items():
            if bar['open'] == 0:
                continue

            data_list.append({
                'symbol': sym,
                'momentum': bar['close'] / bar['open'] - 1.0,  # 因子1：日内动量
                'volatility': (bar['high'] - bar['low']) / bar['open']  # 因子2：日内波动率
            })

        if not data_list:
            return {}

        df = pd.DataFrame(data_list).set_index('symbol')

        # 2. 截面秩标准化 (Cross-Sectional Rank)
        # 转化为 0~1 的百分位排名，消除不同商品量纲差异
        df['mom_rank'] = df['momentum'].rank(pct=True)
        df['vol_rank'] = df['volatility'].rank(pct=True)

        # 3. 合成复合因子得分 (Composite Score)
        # 逻辑假设：偏好强势（高动量得分）且平稳（低波动得分）的品种
        df['composite_score'] = 0.5 * df['mom_rank'] - 0.5 * df['vol_rank']

        # 4. 排序与截断分配
        df = df.sort_values('composite_score', ascending=False)
        symbols_sorted = df.index.tolist()

        # 确保截面品种数量足够分组
        actual_k = min(self.top_k, len(symbols_sorted) // 2)
        if actual_k == 0:
            return {}

        target_weights = {}

        # 做多得分最高的前 K 个
        for sym in symbols_sorted[:actual_k]:
            target_weights[sym] = self.weight_per_leg

        # 做空得分最低的后 K 个
        for sym in symbols_sorted[-actual_k:]:
            target_weights[sym] = -self.weight_per_leg

        return target_weights