# -*- coding: utf-8 -*-
"""
模拟撮合引擎 - 费率与滑点模型 (Fee & Slippage Model)
"""
import os
import sys

# 确保能找到根目录的 config
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

# 交易费率和合约乘数统一来自 config.FEE_DICT。
from config import FEE_DICT
from .order import Offset


class FeeModel:
    """交易摩擦成本计算器"""

    def __init__(self, default_slippage_ticks: int = 1):
        """
        :param default_slippage_ticks: 默认滑点跳数 (设为1，意味着买入价变贵1个tick，卖出价变便宜1个tick)
        """
        self.default_slippage_ticks = default_slippage_ticks

    def _get_meta_data(self, symbol: str) -> dict:
        """从天勤代码提取纯代码，并去 FEE_DICT 里精准查表"""
        raw_code = symbol.split('.')[-1]

        # 智能匹配：原样 -> 大写 -> 小写，确保绝对能命中字典
        meta = FEE_DICT.get(raw_code) or FEE_DICT.get(raw_code.upper()) or FEE_DICT.get(raw_code.lower())

        if not meta:
            print(f"[Fee Model Warning] 品种 {symbol} 未在 FEE_DICT 配置，将使用默认费率。")
            # 兜底防御：给一个极高成本，逼迫回测者去 config 里补全字典
            return {'multiplier': 10, 'tick_size': 1.0,'margin_rate': 0.10, 'fee_type': 'ratio',
                    'fee_open': 0.001, 'fee_close_history': 0.001, 'fee_close_today': 0.005}

        return meta

    def get_symbol_info(self, symbol: str):
        """对外提供乘数和最小变动价位查询"""
        meta = self._get_meta_data(symbol)
        return meta['multiplier'], meta['tick_size']

    def calculate_slippage(self, symbol: str, slippage_ticks: float = None) -> float:
        """
        计算单边滑点劣化的具体绝对数值 (例如螺纹钢 1 跳就是 1.0 元)
        """
        meta = self._get_meta_data(symbol)
        ticks = self.default_slippage_ticks if slippage_ticks is None else float(slippage_ticks)
        slippage_value = meta['tick_size'] * ticks
        return slippage_value

    def calculate_commission(self, symbol: str, price: float, volume: int, offset: Offset) -> float:
        """
        核心物理算账逻辑：计算真实物理手续费

        :param symbol: 品种代码 (如 'KQ.m@SHFE.rb')
        :param price: 真实成交价
        :param volume: 成交手数
        :param offset: 开平标志 (OPEN / CLOSE / CLOSE_TODAY)
        :return: 手续费绝对金额
        """
        meta = self._get_meta_data(symbol)
        multiplier = meta['multiplier']
        fee_type = meta['fee_type']

        # 1. 精准路由：当前这笔单子，到底该用哪个费率？
        if offset == Offset.OPEN:
            rate_or_fixed = meta['fee_open']
        elif offset == Offset.CLOSE_TODAY:
            rate_or_fixed = meta['fee_close_today']
        elif offset == Offset.CLOSE:
            rate_or_fixed = meta['fee_close_history']
        else:
            rate_or_fixed = meta['fee_open']  # 兜底

        # 2. 智能计算：区分“按比例”还是“按固定金额”
        if fee_type == 'ratio':
            # 按金额收费 = 价格 * 手数 * 乘数 * 费率
            commission = price * volume * multiplier * rate_or_fixed
        elif fee_type == 'fixed':
            # 按手收费 = 手数 * 固定金额
            commission = volume * rate_or_fixed
        else:
            commission = 0.0

        return commission
