# -*- coding: utf-8 -*-
"""
Tick 级下单算法基类

TWAP: 纯时间加权平均价格 - 时间分割
VWAP: 成交量加权平均价格 - 成交量驱动
"""
from abc import ABC, abstractmethod
from typing import List, Dict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import uuid


@dataclass
class ExecutionPlan:
    """执行计划：管理一个完整交易信号的执行"""
    plan_id: str
    direction: str           # "open_long", "open_short"
    total_volume: int        # 总手数
    filled_volume: int = 0   # 已成交手数
    avg_price: float = 0.0   # 平均成交价
    signal_price: float = 0.0 # 信号价格
    start_time: datetime = None
    end_time: datetime = None
    status: str = "active"   # active, completed, timeout
    executions: List[Dict] = field(default_factory=list)
    cum_volume: int = 0      # 累计成交量（VWAP用）
    slice_idx: int = 0       # 当前切片索引

    @property
    def remaining_volume(self) -> int:
        return self.total_volume - self.filled_volume

    @property
    def is_complete(self) -> bool:
        return self.filled_volume >= self.total_volume

    def add_execution(self, time: datetime, price: float, volume: int):
        self.executions.append({'time': time, 'price': price, 'volume': volume})
        self.filled_volume += volume
        if self.filled_volume > 0:
            self.avg_price = sum(e['price'] * e['volume'] for e in self.executions) / self.filled_volume


class TickExecutionAlgorithm(ABC):
    """Tick 级下单算法基类"""

    def __init__(self, broker, account, symbol: str):
        self.broker = broker
        self.account = account
        self.symbol = symbol
        self.active_plans: Dict[str, ExecutionPlan] = {}

    @abstractmethod
    def create_plan(self, direction: str, volume: int, signal_time: datetime,
                    signal_price: float, time_window_seconds: int = 300) -> ExecutionPlan:
        pass

    @abstractmethod
    def should_submit_order(self, plan: ExecutionPlan, current_time: datetime, tick: Dict) -> tuple:
        """判断是否需要下单，返回 (是否下单, 本次下单量)"""
        pass

    def on_execution(self, plan: ExecutionPlan, time: datetime, price: float, volume: int):
        plan.add_execution(time, price, volume)
        if plan.is_complete:
            plan.status = "completed"
            self.active_plans.pop(plan.plan_id, None)

    def get_impacted_price_and_volume(self, direction: str, tick: Dict, desired_volume: int) -> tuple:
        """
        市场冲击模型：计算大单成交时的加权平均价和各档成交手数
        默认实现，使用基类的实现
        """
        return MarketExecutor.get_impacted_price_and_volume(self, direction, tick, desired_volume)


class TWAPExecutor(TickExecutionAlgorithm):
    """
    TWAP: 纯时间加权平均价格

    核心逻辑:
    - 把时间窗口均匀分成 N 份
    - 每份时间点下 1/N 的总手数
    - 纯时间分割，与成交量无关

    例如: 买入 30手, 时间窗口300秒, num_slices=10
    - 每个切片下 30/10 = 3手
    - 挂单价格 = 当前 ask（买入）或 bid（卖出）
    """

    def __init__(self, broker, account, symbol: str, num_slices: int = 10):
        super().__init__(broker, account, symbol)
        self.num_slices = num_slices

    def create_plan(self, direction: str, volume: int, signal_time: datetime,
                    signal_price: float, time_window_seconds: int = 300) -> ExecutionPlan:
        plan = ExecutionPlan(
            plan_id=f"TWAP_{uuid.uuid4().hex[:8]}",
            direction=direction, total_volume=volume, signal_price=signal_price,
            start_time=signal_time, end_time=signal_time + timedelta(seconds=time_window_seconds),
        )
        self.active_plans[plan.plan_id] = plan
        return plan

    def should_submit_order(self, plan: ExecutionPlan, current_time: datetime, tick: Dict) -> tuple:
        """纯时间分割：每份时间点下固定数量"""
        if plan.is_complete or current_time > plan.end_time:
            if current_time > plan.end_time:
                plan.status = "timeout"
            return False, 0

        if plan.remaining_volume <= 0:
            return False, 0

        # 计算时间进度
        total_dur = (plan.end_time - plan.start_time).total_seconds()
        if total_dur <= 0:
            return False, 0

        elapsed = (current_time - plan.start_time).total_seconds()
        current_slice = int(elapsed / total_dur * self.num_slices)
        current_slice = min(current_slice, self.num_slices - 1)

        # 每批固定手数 = 总手数 / 批数
        vol_per_slice = plan.total_volume / self.num_slices

        # 本次应下的手数 = 当前批应下 - 已下过的
        target_vol = vol_per_slice * (current_slice + 1)
        submit_vol = target_vol - plan.slice_idx
        submit_vol = int(submit_vol)
        submit_vol = min(submit_vol, plan.remaining_volume)

        if submit_vol > 0:
            plan.slice_idx += submit_vol
            return True, submit_vol

        return False, 0


class VWAPExecutor(TickExecutionAlgorithm):
    """
    VWAP: 成交量加权平均价格

    核心逻辑:
    - 跟踪每个 tick 的成交量
    - 按成交量比例分配下单数量
    - 成交量大的 tick 下更多单

    例如: 买入 30手, 预期总成交量=300
    - Tick 1: vol=100, 下 30 * (100/300) = 10手
    - Tick 2: vol=150, 下 20 * (150/300) = 10手
    - Tick 3: vol=50,  下 10 * (50/300) = 2手
    """

    def __init__(self, broker, account, symbol: str, min_vol_threshold: int = 10):
        super().__init__(broker, account, symbol)
        self.min_vol_threshold = min_vol_threshold

    def create_plan(self, direction: str, volume: int, signal_time: datetime,
                    signal_price: float, time_window_seconds: int = 300) -> ExecutionPlan:
        plan = ExecutionPlan(
            plan_id=f"VWAP_{uuid.uuid4().hex[:8]}",
            direction=direction, total_volume=volume, signal_price=signal_price,
            start_time=signal_time, end_time=signal_time + timedelta(seconds=time_window_seconds),
        )
        self.active_plans[plan.plan_id] = plan
        return plan

    def should_submit_order(self, plan: ExecutionPlan, current_time: datetime, tick: Dict) -> tuple:
        """按成交量比例分配下单数量"""
        if plan.is_complete or current_time > plan.end_time:
            if current_time > plan.end_time:
                plan.status = "timeout"
            return False, 0

        if plan.remaining_volume <= 0:
            return False, 0

        # 获取当前 tick 成交量
        tick_vol = int(tick.get('volume', 0))

        # 成交量太小不下单，避免在流动性差时成交
        if tick_vol < self.min_vol_threshold:
            return False, 0

        # 累计成交量
        plan.cum_volume += tick_vol

        # 预估总成交量（用于计算比例）
        # 如果是第一个 tick，预估一个值
        # 否则用已累计的 + 当前 tick
        if plan.cum_volume == tick_vol:
            est_total_vol = tick_vol * 10  # 简单预估
        else:
            est_total_vol = plan.cum_volume * 1.5  # 基于已累计的

        # 按成交量比例计算本次应下多少手
        ratio = min(tick_vol / est_total_vol, 0.5)  # 单次不超过50%
        submit_vol = int(plan.remaining_volume * ratio)

        # 至少下1手，避免永远下不出去
        if submit_vol == 0 and plan.remaining_volume > 0:
            submit_vol = plan.remaining_volume

        submit_vol = min(submit_vol, plan.remaining_volume)

        if submit_vol > 0:
            return True, submit_vol

        return False, 0


class MarketExecutor(TickExecutionAlgorithm):
    """
    市价单：立即成交

    核心逻辑:
    - 买入: 按 ask（卖一价）成交，滑点 = 1 tick
    - 卖出: 按 bid（买一价）成交，滑点 = 1 tick
    """

    def __init__(self, broker, account, symbol: str):
        super().__init__(broker, account, symbol)

    def create_plan(self, direction: str, volume: int, signal_time: datetime,
                    signal_price: float, time_window_seconds: int = 300) -> ExecutionPlan:
        plan = ExecutionPlan(
            plan_id=f"MKT_{uuid.uuid4().hex[:8]}",
            direction=direction, total_volume=volume, signal_price=signal_price,
            start_time=signal_time, end_time=signal_time + timedelta(seconds=time_window_seconds),
        )
        self.active_plans[plan.plan_id] = plan
        return plan

    def should_submit_order(self, plan: ExecutionPlan, current_time: datetime, tick: Dict) -> tuple:
        """市价单：立即全量下单"""
        if plan.is_complete or current_time > plan.end_time:
            return False, 0
        return True, plan.remaining_volume

    def get_market_exec_price(self, direction: str, tick: Dict) -> float:
        """
        真实市价单成交价
        买入 → ask（卖一价）
        卖出 → bid（买一价）
        滑点 = 1 tick = ask - bid
        """
        last_price = float(tick.get('last_price', 0))
        bid = float(tick.get('bid_price_1', last_price))
        ask = float(tick.get('ask_price_1', last_price))

        if 'long' in direction.lower():
            return ask  # 买入按卖一价，滑点 = ask - bid = 1 tick
        else:
            return bid  # 卖出按买一价，滑点 = ask - bid = 1 tick

    def get_impacted_price_and_volume(self, direction: str, tick: Dict, desired_volume: int) -> tuple:
        """
        市场冲击模型：计算大单成交时的加权平均价和各档成交手数

        由于只有1档数据，模拟多档盘口：
        - 假设1档量约等于平均盘口量的50%，用于模拟真实流动性
        - 各档衰减系数 (1.0, 0.6, 0.4, 0.3, 0.25, ...)
        - 每超出一档，价格移动 1 tick

        Returns:
            (avg_price, filled_per_level: list of (price, volume))
        """
        last_price = float(tick.get('last_price', 0))
        bid = float(tick.get('bid_price_1', last_price))
        ask = float(tick.get('ask_price_1', last_price))
        base_vol = int(tick.get('bid_volume_1', 0))  # 1档盘口量

        # 螺纹钢期货每手10吨，100手=1000吨是大单
        # 假设1档实际能成交的比例（真实市场中，大单往往超出1档）
        # 使用较小的1档量来模拟市场冲击
        if base_vol <= 0:
            base_vol = 10
        # 大单情况下，假设只有30%的量在1档，其余需要滑移
        effective_vol = max(10, int(base_vol * 0.3))

        is_buy = 'long' in direction.lower()

        # 模拟各档盘口量（衰减）
        levels = []
        remaining = desired_volume
        price = ask if is_buy else bid
        decay_factors = [1.0, 0.6, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1]  # 各档衰减系数

        for i, decay in enumerate(decay_factors):
            if remaining <= 0:
                break
            level_vol = max(1, int(effective_vol * decay))
            filled_vol = min(remaining, level_vol)
            levels.append((price, filled_vol))
            remaining -= filled_vol
            price += 1 if is_buy else -1  # 往不利方向移动1跳

        if remaining > 0:
            # 还有剩余，向更远端扩展
            last_price_level = levels[-1][0] if levels else price
            extra_dir = 1 if is_buy else -1
            while remaining > 0:
                filled_vol = min(remaining, effective_vol)
                levels.append((last_price_level + extra_dir, filled_vol))
                remaining -= filled_vol
                last_price_level += extra_dir

        # 计算加权平均价
        total_value = sum(p * v for p, v in levels)
        total_vol = sum(v for _, v in levels)
        avg_price = total_value / total_vol if total_vol > 0 else price

        return avg_price, levels
