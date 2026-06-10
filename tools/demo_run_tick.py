# -*- coding: utf-8 -*-
"""
Tick 级别下单算法测试
"""
import os
import sys
import pandas as pd
import uuid
from datetime import datetime

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from data_feed.data_provider import DataProvider
from portfolio.account import Account
from broker.match_engine import MatchEngine
from broker.order import Order, OrderType, Direction, Offset, OrderStatus, Trade
from tick_algorithms import MarketExecutor, TWAPExecutor, VWAPExecutor


def extract_tick(tick_row) -> dict:
    tick = {}
    if isinstance(tick_row, pd.Series):
        for col in tick_row.index:
            if isinstance(col, tuple):
                tick[col[0]] = tick_row[col]
            else:
                tick[col] = tick_row[col]
    return tick


def run_tick_execution(df_tick: pd.DataFrame, signals: list, executor_class, algo_name: str, **kwargs) -> dict:
    account = Account(initial_capital=5000000)
    broker = MatchEngine(account=account)
    executor = executor_class(broker, account, 'rb', **kwargs)

    signal_idx = 0
    pending_orders = {}
    executions = []

    for idx, (current_time, row) in enumerate(df_tick.iterrows()):
        tick = extract_tick(row)
        if pd.isna(tick.get('last_price', None)):
            continue

        last_price = float(tick['last_price'])
        bid_price = float(tick.get('bid_price_1', last_price))
        ask_price = float(tick.get('ask_price_1', last_price))

        # 处理待成交订单
        for plan_id, (order, expected_vol, limit_price) in list(pending_orders.items()):
            is_filled = False
            exec_price = 0.0

            if order.direction == Direction.LONG:
                if ask_price <= limit_price and ask_price > 0:
                    is_filled = True
                    exec_price = ask_price
            else:
                if bid_price >= limit_price and bid_price > 0:
                    is_filled = True
                    exec_price = bid_price

            if is_filled:
                order.status = OrderStatus.FILLED
                order.filled_volume = expected_vol
                order.filled_price = exec_price

                commission = broker.fee_model.calculate_commission(
                    symbol=order.symbol, price=exec_price,
                    volume=expected_vol, offset=order.offset
                )

                trade = Trade(
                    symbol=order.symbol, direction=order.direction, offset=order.offset,
                    volume=expected_vol, price=exec_price, trade_time=current_time,
                    commission=commission, slippage_cost=0.0, order_id=order.order_id
                )
                broker.trade_history.append(trade)
                account.release_pending_margin(order, limit_price)
                account.process_trade(trade)

                executor.on_execution(executor.active_plans.get(plan_id), current_time, exec_price, expected_vol)

                executions.append({
                    'time': current_time,
                    'direction': order.direction.value,
                    'volume': expected_vol,
                    'exec_price': exec_price,
                    'signal_price': order._signal_price,
                })

                pending_orders.pop(plan_id)

        # 新信号 - 使用精确的时间匹配
        while signal_idx < len(signals):
            sig = signals[signal_idx]
            sig_time = sig['time']

            # 精确匹配时间（避免浮点数问题）
            if current_time == sig_time:
                executor.create_plan(
                    direction=sig['action'], volume=sig['volume'],
                    signal_time=sig_time, signal_price=float(sig['price']),
                    time_window_seconds=300
                )
                sig['plan_id'] = list(executor.active_plans.keys())[-1]
                print(f"\n[Tick Demo] {current_time} | Signal #{signal_idx+1}: {sig['action']} {sig['volume']}手 @ {sig['price']:.0f}")
                print(f"    bid={bid_price:.0f} ask={ask_price:.0f}")
                signal_idx += 1
            elif current_time > sig_time:
                # 时间已过但未匹配，说明时间不匹配，跳过
                print(f"\n[Tick Demo Warning] {current_time} | 跳过 Signal #{signal_idx+1}，时间不匹配。")
                signal_idx += 1
            else:
                # 时间未到
                break

        # 检查下单
        for plan_id, plan in list(executor.active_plans.items()):
            if plan.status != "active":
                continue

            # 构建完整的 tick 数据（包含 bid_vol 和 ask_vol）
            full_tick = {
                'last_price': last_price,
                'bid_price_1': bid_price,
                'ask_price_1': ask_price,
                'bid_volume_1': tick.get('bid_volume_1', 0),
                'ask_volume_1': tick.get('ask_volume_1', 0),
                'volume': tick.get('volume', 0),
            }

            dir_map = {"open_long": Direction.LONG, "open_short": Direction.SHORT}
            offset_map = {"open_long": Offset.OPEN, "open_short": Offset.OPEN}
            dir_enum = dir_map[plan.direction]
            offset_enum = offset_map[plan.direction]

            # 检查时间窗口是否快结束
            total_dur = (plan.end_time - plan.start_time).total_seconds()
            remaining_seconds = (plan.end_time - current_time).total_seconds()
            time_ratio = remaining_seconds / total_dur if total_dur > 0 else 0
            force_market = time_ratio < 0.1 or remaining_seconds <= 0

            # 如果 pending 中有未成交订单且时间快到，强制市价平仓
            if plan_id in pending_orders and force_market:
                order, submit_vol, _ = pending_orders[plan_id]
                if plan.remaining_volume > 0:
                    avg_price, levels = executor.get_impacted_price_and_volume(
                        plan.direction, full_tick, plan.remaining_volume
                    )
                    exec_price = avg_price

                    commission = broker.fee_model.calculate_commission(
                        symbol='rb', price=exec_price, volume=plan.remaining_volume, offset=offset_enum
                    )
                    trade = Trade(
                        symbol='rb', direction=dir_enum, offset=offset_enum,
                        volume=plan.remaining_volume, price=exec_price, trade_time=current_time,
                        commission=commission, slippage_cost=0.0, order_id=f"MKT_{uuid.uuid4().hex[:8]}"
                    )
                    broker.trade_history.append(trade)
                    account.process_trade(trade)
                    executor.on_execution(plan, current_time, exec_price, plan.remaining_volume)

                    level_details = '; '.join([f"@{p}:{v}手" for p, v in levels[:5]]) if len(levels) > 5 else '; '.join([f"@{p}:{v}手" for p, v in levels])
                    executions.append({
                        'time': current_time,
                        'direction': dir_enum.value,
                        'volume': plan.remaining_volume,
                        'exec_price': exec_price,
                        'signal_price': plan.signal_price,
                        'bid_vol': full_tick['bid_volume_1'],
                        'ask_vol': full_tick['ask_volume_1'],
                    })

                    side = "买入" if dir_enum == Direction.LONG else "卖出"
                    slippage = exec_price - plan.signal_price if dir_enum == Direction.LONG else plan.signal_price - exec_price
                    print(f"  [{algo_name}] 强制市价平仓: {side}{plan.remaining_volume}手 @ 均{exec_price:.1f} {level_details} 滑点:{slippage:+.0f}")

                    del pending_orders[plan_id]
                    continue

            if plan_id in pending_orders:
                continue

            should_submit, vol = executor.should_submit_order(plan, current_time, full_tick)

            if not should_submit or vol <= 0 or plan.remaining_volume <= 0:
                continue

            # 区分市价单和限价单的成交方式
            if isinstance(executor, MarketExecutor):
                # 市价单：考虑市场冲击
                vol = plan.remaining_volume
                avg_price, levels = executor.get_impacted_price_and_volume(
                    plan.direction, full_tick, vol
                )
                exec_price = avg_price

                commission = broker.fee_model.calculate_commission(
                    symbol='rb', price=exec_price, volume=vol, offset=offset_enum
                )
                trade = Trade(
                    symbol='rb', direction=dir_enum, offset=offset_enum,
                    volume=vol, price=exec_price, trade_time=current_time,
                    commission=commission, slippage_cost=0.0, order_id=f"MKT_{uuid.uuid4().hex[:8]}"
                )
                broker.trade_history.append(trade)
                account.process_trade(trade)
                executor.on_execution(plan, current_time, exec_price, vol)

                # 记录每档成交明细
                level_details = '; '.join([f"@{p}:{v}手" for p, v in levels[:5]]) if len(levels) > 5 else '; '.join([f"@{p}:{v}手" for p, v in levels])

                executions.append({
                    'time': current_time,
                    'direction': dir_enum.value,
                    'volume': vol,
                    'exec_price': exec_price,
                    'signal_price': plan.signal_price,
                    'bid_vol': full_tick['bid_volume_1'],
                    'ask_vol': full_tick['ask_volume_1'],
                })

                side = "买入" if dir_enum == Direction.LONG else "卖出"
                slippage = exec_price - plan.signal_price if dir_enum == Direction.LONG else plan.signal_price - exec_price
                print(f"  {algo_name} 立即成交: {side}{vol}手 @ 均{exec_price:.1f} {level_details} 滑点:{slippage:+.0f}")
            else:
                # TWAP/VWAP：用即时 bid/ask 挂单（追踪市场）
                # 检查是否快到时间窗口末尾，如果是则强制市价成交
                remaining_seconds = (plan.end_time - current_time).total_seconds()
                time_ratio = remaining_seconds / (plan.end_time - plan.start_time).total_seconds()

                # 如果剩余时间 < 10% 或者已经超时，强制市价成交剩余部分
                force_market = time_ratio < 0.1 or remaining_seconds <= 0

                if force_market and plan.remaining_volume > 0:
                    # 强制市价成交剩余部分
                    vol = plan.remaining_volume
                    avg_price, levels = executor.get_impacted_price_and_volume(
                        plan.direction, full_tick, vol
                    )
                    exec_price = avg_price

                    commission = broker.fee_model.calculate_commission(
                        symbol='rb', price=exec_price, volume=vol, offset=offset_enum
                    )
                    trade = Trade(
                        symbol='rb', direction=dir_enum, offset=offset_enum,
                        volume=vol, price=exec_price, trade_time=current_time,
                        commission=commission, slippage_cost=0.0, order_id=f"MKT_{uuid.uuid4().hex[:8]}"
                    )
                    broker.trade_history.append(trade)
                    account.process_trade(trade)
                    executor.on_execution(plan, current_time, exec_price, vol)

                    level_details = '; '.join([f"@{p}:{v}手" for p, v in levels[:5]]) if len(levels) > 5 else '; '.join([f"@{p}:{v}手" for p, v in levels])
                    executions.append({
                        'time': current_time,
                        'direction': dir_enum.value,
                        'volume': vol,
                        'exec_price': exec_price,
                        'signal_price': plan.signal_price,
                        'bid_vol': full_tick['bid_volume_1'],
                        'ask_vol': full_tick['ask_volume_1'],
                    })

                    side = "买入" if dir_enum == Direction.LONG else "卖出"
                    slippage = exec_price - plan.signal_price if dir_enum == Direction.LONG else plan.signal_price - exec_price
                    print(f"  [{algo_name}] 强制市价成交: {side}{vol}手 @ 均{exec_price:.1f} {level_details} 滑点:{slippage:+.0f}")
                    continue
                else:
                    # 正常挂限价单
                    # 买入挂即时 ask（卖一价）
                    # 卖出挂即时 bid（买一价）
                    if dir_enum == Direction.LONG:
                        limit_price = ask_price
                    else:
                        limit_price = bid_price

                    submit_vol = min(vol, plan.remaining_volume)

                order = Order(
                    symbol='rb', direction=dir_enum, offset=offset_enum,
                    volume=submit_vol, price=limit_price, insert_time=current_time,
                    order_type=OrderType.LIMIT
                )
                order._signal_price = plan.signal_price

                # broker.insert_order 内部会冻结保证金，不需要重复调用
                broker.insert_order(order, limit_price)
                pending_orders[plan_id] = (order, submit_vol, limit_price)

                side = "买入" if dir_enum == Direction.LONG else "卖出"
                print(f"  {algo_name} 下单: {side}{submit_vol}手 @ 限价{limit_price:.0f} (bid={bid_price:.0f} ask={ask_price:.0f})")

        # 撮合 pending_orders 中的限价单
        if pending_orders:
            bar_data = {
                'rb': {
                    'open': bid_price,
                    'high': max(bid_price, ask_price),
                    'low': min(bid_price, ask_price),
                    'close': bid_price,
                }
            }
            new_trades = broker.process_cross_section(current_time, bar_data)

            # 处理新成交
            for trade in new_trades:
                # 找到对应的 plan
                for plan_id, plan in list(executor.active_plans.items()):
                    if plan_id in pending_orders:
                        order, _, _ = pending_orders[plan_id]
                        if order.direction == trade.direction:
                            executor.on_execution(plan, current_time, trade.price, trade.volume)
                            executions.append({
                                'time': current_time,
                                'direction': trade.direction.value,
                                'volume': trade.volume,
                                'exec_price': trade.price,
                                'signal_price': order._signal_price if hasattr(order, '_signal_price') else plan.signal_price,
                                'bid_vol': tick.get('bid_volume_1', 0),
                                'ask_vol': tick.get('ask_volume_1', 0),
                            })
                            if plan.remaining_volume <= 0:
                                del pending_orders[plan_id]
                            break

    # 结果
    if not executions:
        return {
            'executions': [], 'signals': signals,
            'exec_df': pd.DataFrame(columns=['time', 'direction', 'volume', 'exec_price', 'signal_price']),
            'total_slippage': 0, 'avg_slippage': 0
        }

    exec_df = pd.DataFrame(executions)
    exec_df['slippage'] = exec_df.apply(
        lambda r: r['exec_price'] - r['signal_price'] if 'LONG' in r['direction'] else r['signal_price'] - r['exec_price'], axis=1)

    print(f"\n[Tick Demo] {algo_name} 成交明细:")
    print(f"   {'时间':<20} {'方向':<6} {'手数':>4} {'信号价':>8} {'成交价':>8} {'滑点':>8}")
    print("   " + "-" * 60)
    for _, row in exec_df.iterrows():
        flag = "✓" if row['slippage'] >= 0 else "✗"
        print(f"   {str(row['time']):<20} {row['direction']:<6} {row['volume']:>4} {row['signal_price']:>8.0f} {row['exec_price']:>8.0f} {row['slippage']:>+8.0f} {flag}")

    return {
        'executions': executions, 'exec_df': exec_df, 'signals': signals,
        'total_slippage': exec_df['slippage'].sum(),
        'avg_slippage': exec_df['slippage'].mean()
    }


def main():
    print("=" * 70)
    print("Tick 级别下单算法测试")
    print("=" * 70)

    provider = DataProvider()
    print("\n[Tick Demo] 获取 Tick 数据...")
    df_tick = provider.get_history(
        symbols=['KQ.m@SHFE.rb'],
        start_date='2025-02-25 09:00:00',
        end_date='2025-02-25 10:00:00',
        freq='tick', data_type='main'
    )

    if df_tick.empty:
        print("[Tick Demo Error] 没有 Tick 数据。")
        return

    print(f"[Tick Demo] Tick 数据: {len(df_tick)} 条")

    # 生成20个测试信号，分布在数据范围内
    import numpy as np
    np.random.seed(42)  # 可重复

    num_signals = 5  # 减少信号数，更清晰
    signal_indices = np.linspace(50, len(df_tick)-100, num_signals, dtype=int)

    # 验证列名获取是否正确
    test_bid_col = None
    test_ask_col = None
    for col in df_tick.columns:
        if isinstance(col, tuple):
            if 'bid_price_1' in col:
                test_bid_col = col
            elif 'ask_price_1' in col:
                test_ask_col = col

    print(f"验证: bid_col={test_bid_col}, ask_col={test_ask_col}")
    if not test_bid_col or not test_ask_col:
        print("[Tick Demo Warning] bid/ask 列未找到，使用默认值。")
        test_bid_col = test_ask_col = None

    signals = []
    for i, idx in enumerate(signal_indices):
        row = df_tick.iloc[idx]

        # 尝试获取 bid/ask
        if test_bid_col and test_ask_col:
            bid = float(row[test_bid_col])
            ask = float(row[test_ask_col])
        else:
            # 回退到 last_price 附近
            last = float(row.get(('last_price', df_tick.columns[0]), 3311))
            bid = last - 1
            ask = last + 1

        # 随机多空方向，信号价用实际的 bid/ask
        if i % 3 == 0:
            action = 'open_long'
            price = ask  # 买入：信号价 = ask
        elif i % 3 == 1:
            action = 'open_short'
            price = bid  # 卖出：信号价 = bid
        else:
            action = 'open_long'
            price = ask

        signals.append({
            'time': df_tick.index[idx],
            'action': action,
            'price': float(price),
            'volume': 100,  # 大单量测试
        })

    # 调试：打印前几个信号的详细信息
    print(f"\n调试信息 - 前5个信号:")
    for i in range(min(5, len(signals))):
        sig = signals[i]
        print(f"  Signal {i+1}: time={sig['time']}, action={sig['action']}, price={sig['price']}")

    print(f"\n生成 {len(signals)} 个测试信号")

    print("\n" + "=" * 70)
    print("算法对比测试")
    print("=" * 70)

    results = {}
    for algo_name, executor_class, kwargs in [
        ("Market(市价)", MarketExecutor, {}),
        ("TWAP(10切片)", TWAPExecutor, {'num_slices': 10}),
        ("VWAP(最小量10)", VWAPExecutor, {'min_vol_threshold': 10}),
    ]:
        print(f"\n{'='*65}\n【{algo_name}】\n{'='*65}")
        result = run_tick_execution(df_tick.copy(), [s.copy() for s in signals], executor_class, algo_name, **kwargs)
        results[algo_name] = result
        exec_df = result['exec_df']
        total_vol = int(exec_df['volume'].sum()) if len(exec_df) > 0 else 0
        print(f"\n信号数: {len(signals)} | 下单手数: {sum(s['volume'] for s in signals)} | 成交手数: {total_vol} | 成交次数: {len(exec_df)}")

    print("\n" + "=" * 70)
    print("算法对比总结")
    print("=" * 70)
    print(f"{'算法':<20} {'下单手数':>8} {'成交手数':>8} {'成交次数':>8} {'均滑点':>10} {'总滑点':>10}")
    print("-" * 70)
    total_target = sum(s['volume'] for s in signals)
    for algo_name, result in results.items():
        exec_df = result['exec_df']
        total_volume = int(exec_df['volume'].sum())
        trade_count = len(exec_df)
        print(f"{algo_name:<20} {total_target:>8} {total_volume:>8} {trade_count:>8} {result.get('avg_slippage', 0):>+10.0f} {result.get('total_slippage', 0):>+10.0f}")


if __name__ == "__main__":
    main()
