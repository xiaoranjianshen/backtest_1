# -*- coding: utf-8 -*-
"""
测试 Market Impact Model 的冷酷逻辑

验证：
1. 市价单立即成交的场景
2. TWAP 分批成交的场景
3. 不同 impact_step_vol 对冲击的影响
"""
import sys
sys.path.insert(0, '.')

from backtest_1.tick_impact_model import MarketImpactModel, ExecutionPlan
from datetime import datetime, timedelta

# 创建冲击模型
impact = MarketImpactModel(tick_size=1.0, impact_step_vol=20)

print("=" * 70)
print("Market Impact Model 测试")
print("=" * 70)

# 测试1：正常盘口，100手买入
print("\n【测试1】正常盘口，买入100手")
tick1 = {
    'bid_price_1': 3311,
    'ask_price_1': 3312,
    'bid_volume_1': 100,
    'ask_volume_1': 80,  # 卖一只有80手
    'last_price': 3311.5
}

arrival = impact.get_arrival_price(tick1)
print(f"盘口：bid=3311(量80), ask=3312(量80)")
print(f"到达中间价 = {arrival:.1f}")

result = impact.calculate_execution("open_long", tick1, 100, arrival)
avg_price, slippage, detail = result

print(f"成交均价 = {avg_price:.2f}")
print(f"滑点 = {slippage:.2f} (相对于 mid price)")
print(f"成交明细：{detail}")

print("\n解读：")
print(f"  - 卖一(3312)有80手，吃80手")
print(f"  - 卖二(3313)有20手，吃20手")
print(f"  - 均价 = (80*3312 + 20*3313) / 100 = {avg_price:.2f}")

# 测试2：深度市场，100手买入
print("\n" + "=" * 70)
print("\n【测试2】深度市场，买入100手 (impact_step_vol=50)")
impact_deep = MarketImpactModel(tick_size=1.0, impact_step_vol=50)

arrival = impact_deep.get_arrival_price(tick1)
result = impact_deep.calculate_execution("open_long", tick1, 100, arrival)
avg_price, slippage, detail = result

print(f"成交均价 = {avg_price:.2f}")
print(f"滑点 = {slippage:.2f}")
print(f"成交明细：{detail}")

print("\n解读：impact_step_vol=50 表示市场深度更好，滑点更小")

# 测试3：TWAP 分批买入
print("\n" + "=" * 70)
print("\n【测试3】TWAP 场景：100手分5批买入，每批20手")

signal_time = datetime(2025, 2, 25, 9, 0, 0)
end_time = signal_time + timedelta(minutes=5)

# 模拟价格逐渐上涨
prices = [
    (3311, 3312, 80, 80),   # 09:00
    (3312, 3313, 80, 80),   # 09:01
    (3313, 3314, 80, 80),   # 09:02
    (3314, 3315, 80, 80),   # 09:03
    (3315, 3316, 80, 80),   # 09:04
]

arrival = (3311 + 3312) / 2  # 信号时刻的中间价
print(f"信号时刻到达中间价 = {arrival:.1f}")

total_cost = 0
for i, (bid, ask, bv, av) in enumerate(prices):
    tick = {
        'bid_price_1': bid,
        'ask_price_1': ask,
        'bid_volume_1': bv,
        'ask_volume_1': av,
        'last_price': (bid + ask) / 2
    }

    result = impact.calculate_execution("open_long", tick, 20, arrival)
    avg_price, slippage, detail = result
    total_cost += avg_price * 20

    print(f"  第{i+1}批: bid={bid}, ask={ask}, 均价={avg_price:.1f}, 滑点={slippage:.2f}")

final_avg = total_cost / 100
final_slippage = final_avg - arrival
print(f"\n总成交均价 = {final_avg:.2f}")
print(f"总滑点 = {final_slippage:.2f} (相对于信号时刻的 mid={arrival})")

# 测试4：市价单 vs TWAP 对比
print("\n" + "=" * 70)
print("\n【测试4】市价单 vs TWAP 对比（价格单边上涨场景）")

# 场景：信号后价格持续上涨
tick_base = {
    'bid_price_1': 3311,
    'ask_price_1': 3312,
    'bid_volume_1': 100,
    'ask_volume_1': 100,
    'last_price': 3311.5
}
arrival = impact.get_arrival_price(tick_base)

print(f"\n信号时刻: bid=3311, ask=3312, arrival_mid={arrival:.1f}")

# 市价单：立即成交100手
result_market = impact.calculate_execution("open_long", tick_base, 100, arrival)
print(f"\n[市价单] 立即成交100手:")
print(f"  均价={result_market[0]:.2f}, 滑点={result_market[1]:.2f}")
print(f"  明细: {result_market[2]}")

# TWAP：分5批，每批20手，但价格每批+1跳
print(f"\n[TWAP] 分5批成交，每批20手:")
total_slippage_twap = 0
for i in range(5):
    # 价格每批上涨1跳
    tick_twap = {
        'bid_price_1': 3311 + i,
        'ask_price_1': 3312 + i,
        'bid_volume_1': 100,
        'ask_volume_1': 100,
        'last_price': 3311.5 + i
    }
    result = impact.calculate_execution("open_long", tick_twap, 20, arrival)
    total_slippage_twap += result[1] * 20
    print(f"  第{i+1}批: ask={3312+i}, 均价={result[0]:.2f}, 滑点={result[1]:.2f}")

print(f"\n[TWAP] 总滑点 = {total_slippage_twap:.2f}")

print("\n" + "=" * 70)
print("结论：")
print("- 如果市场上涨，TWAP 比市价单更差（越晚买越贵）")
print("- 如果市场下跌，TWAP 比市价单更好（越晚买越便宜）")
print("- TWAP 的滑点来源于时间风险，不是市场冲击")
print("=" * 70)
