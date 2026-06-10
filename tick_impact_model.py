# -*- coding: utf-8 -*-
"""
微观市场冲击模型 (Market Impact Model) & 执行计划基类

核心：采用主动成交 (Taker) 的盘口深度穿透模型估计执行成本。

与现有结构整合：
- ExecutionPlan 与 tick_algorithms/base.py 保持兼容
- calculate_execution 只返回按盘口深度估算后的成交结果
- TWAP/VWAP 只做调度器，调用此函数获取成交价
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Tuple


@dataclass
class ExecutionPlan:
    """
    执行计划：管理一个完整交易信号的生命周期

    兼容现有 tick_algorithms/base.py 的字段定义
    新增：arrival_price 作为唯一绝对基准
    """
    plan_id: str
    direction: str           # "open_long" 或 "open_short"
    total_volume: int        # 总期望下单手数

    # 到达中间价 (Arrival Price)
    # 信号发出的那一瞬间，(bid + ask) / 2
    # 这是评价算法好坏的唯一绝对基准
    arrival_price: float

    start_time: datetime
    end_time: datetime

    # 以下为兼容现有结构
    filled_volume: int = 0
    avg_price: float = 0.0
    signal_price: float = 0.0
    status: str = "active"
    executions: List[Dict] = field(default_factory=list)
    cum_volume: int = 0      # 累计成交量（VWAP用）
    slice_idx: int = 0       # 当前切片索引

    @property
    def remaining_volume(self) -> int:
        return self.total_volume - self.filled_volume

    @property
    def is_complete(self) -> bool:
        return self.filled_volume >= self.total_volume

    def add_execution(self, time: datetime, price: float, volume: int,
                     slippage: float = 0.0, detail: str = ""):
        """记录一次成交，含滑点信息"""
        self.executions.append({
            'time': time,
            'price': price,
            'volume': volume,
            'slippage': slippage,
            'detail': detail
        })
        self.filled_volume += volume
        if self.filled_volume > 0:
            self.avg_price = sum(e['price'] * e['volume'] for e in self.executions) / self.filled_volume

    def get_total_slippage(self) -> float:
        """计算相对于 arrival_price 的总滑点（正数=亏损）"""
        if self.filled_volume == 0:
            return 0.0
        is_buy = 'long' in self.direction.lower()
        if is_buy:
            return (self.avg_price - self.arrival_price) * self.filled_volume
        else:
            return (self.arrival_price - self.avg_price) * self.filled_volume

    def get_avg_slippage_per_unit(self) -> float:
        """平均每手滑点"""
        if self.filled_volume == 0:
            return 0.0
        return self.get_total_slippage() / self.filled_volume


class MarketImpactModel:
    """
    微观市场冲击模型

    模拟大单穿透盘口 (Walk the Book) 的成交过程。

    使用方法：
        impact = MarketImpactModel(tick_size=1.0, impact_step_vol=20)
        avg_price, slippage, detail = impact.calculate_execution(
            direction="open_long",
            tick=current_tick,
            target_volume=100,
            arrival_price=arrival_mid
        )
    """

    def __init__(self, tick_size: float = 1.0, impact_step_vol: int = 20):
        """
        :param tick_size: 品种的最小变动价位 (螺纹钢=1.0, 黄金=0.02)
        :param impact_step_vol: 当吃干第一档后，每向深处吃多少手，价格恶化1跳
                              这个值越大，说明市场深度越好
        """
        self.tick_size = tick_size
        self.impact_step_vol = impact_step_vol

    def get_arrival_price(self, tick: Dict) -> float:
        """
        获取到达基准价 (Mid-Price)

        Args:
            tick: 包含 bid_price_1, ask_price_1 的 Tick 数据

        Returns:
            (bid + ask) / 2
        """
        last_price = float(tick.get('last_price', 0))
        bid = float(tick.get('bid_price_1', last_price))
        ask = float(tick.get('ask_price_1', last_price))
        return (bid + ask) / 2.0

    def calculate_execution(self, direction: str, tick: Dict,
                          target_volume: int, arrival_price: float) -> Tuple[float, float, str]:
        """
        市价单成交成本估算

        输入目标成交量，返回按盘口深度估算后的成交均价。

        Args:
            direction: "open_long" 或 "open_short"
            tick: 当前 Tick 数据，必须包含 bid_price_1, ask_price_1, bid_volume_1, ask_volume_1
            target_volume: 想要成交的手数
            arrival_price: 信号时刻的 (bid+ask)/2，用于计算滑点

        Returns:
            Tuple[avg_price, slippage, detail]:
            - avg_price: 成交均价
            - slippage: 相对于 arrival_price 的滑点（正数=亏损）
            - detail: 各档成交明细，格式 "@3312x20 -> @3313x30 -> @3314x50"
        """
        is_buy = 'long' in direction.lower() or 'buy' in direction.lower()

        # 提取真实盘口数据
        last_price = float(tick.get('last_price', 0))
        bid = float(tick.get('bid_price_1', last_price))
        ask = float(tick.get('ask_price_1', last_price))
        bid_vol = int(tick.get('bid_volume_1', 0))
        ask_vol = int(tick.get('ask_volume_1', 0))

        # 兜底容错
        if bid_vol <= 0:
            bid_vol = 10
        if ask_vol <= 0:
            ask_vol = 10
        if bid <= 0 or ask <= 0:
            # 极端情况，用 last_price
            bid = last_price - self.tick_size
            ask = last_price + self.tick_size

        remaining = target_volume
        total_cost = 0.0
        levels = []

        # 确定起步价和起步容量
        if is_buy:
            current_price = ask
            current_vol_avail = ask_vol
        else:
            current_price = bid
            current_vol_avail = bid_vol

        # 主动成交并逐档穿透盘口 (Walk the Order Book)。
        while remaining > 0:
            filled_at_this_level = min(remaining, current_vol_avail)
            total_cost += filled_at_this_level * current_price
            levels.append(f"@{current_price:.1f}x{filled_at_this_level}")

            remaining -= filled_at_this_level

            # 如果当前档被吃干了，向更深层要流动性
            if remaining > 0:
                # 往不利方向移动1跳
                current_price += self.tick_size if is_buy else -self.tick_size
                # 假设深层每档能承接 impact_step_vol 手
                current_vol_avail = self.impact_step_vol

        # 计算加权均价
        avg_exec_price = total_cost / target_volume

        # 计算绝对滑点（相对于 arrival_price）
        # 正数 = 亏损/摩擦成本
        if is_buy:
            slippage = avg_exec_price - arrival_price
        else:
            slippage = arrival_price - avg_exec_price

        detail_str = " -> ".join(levels)
        return avg_exec_price, slippage, detail_str

    def simulate_instant_market_order(self, direction: str, tick: Dict,
                                     volume: int) -> Dict:
        """
        模拟市价单立即全部成交（用于 Market 算法）

        Args:
            direction: "open_long" 或 "open_short"
            tick: 当前 Tick 数据
            volume: 下单手数

        Returns:
            Dict: {
                'avg_price': 成交均价,
                'slippage': 滑点（相对于 mid price）,
                'slippage_per_unit': 每手滑点,
                'total_slippage': 总滑点金额,
                'detail': 成交明细,
                'arrival_price': 到达中间价
            }
        """
        arrival_price = self.get_arrival_price(tick)
        avg_price, slippage, detail = self.calculate_execution(
            direction, tick, volume, arrival_price
        )

        is_buy = 'long' in direction.lower()
        slippage_per_unit = slippage  # 已经是每手滑点
        total_slippage = slippage * volume  # 总滑点

        return {
            'avg_price': avg_price,
            'slippage': slippage,
            'slippage_per_unit': slippage_per_unit,
            'total_slippage': total_slippage,
            'detail': detail,
            'arrival_price': arrival_price
        }
