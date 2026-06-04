# -*- coding: utf-8 -*-
"""
核心调度主循环 (纯净接口版)
"""
import os
import sys
import pandas as pd
import re

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from data_feed.data_provider import DataProvider
from portfolio.account import Account
from broker.match_engine import MatchEngine
from broker.rollover import MainContractRollover
from analyzer.performance import StrategyAnalyzer
from config import build_query_symbol, FEE_DICT, pure_product_code


def extract_bar_data(row, columns_level_1):
    bar_data = {}
    for full_sym in columns_level_1:
        match = re.search(r'\((.*?)\)', full_sym)
        if not match:
            continue
        raw_code = match.group(1).lower()
        if raw_code not in bar_data:
            bar_data[raw_code] = {}

        if ('close', full_sym) in row and not pd.isna(row[('close', full_sym)]):
            bar_data[raw_code]['close'] = row[('close', full_sym)]
            bar_data[raw_code]['open'] = row[('open', full_sym)] if ('open', full_sym) in row else bar_data[raw_code]['close']
            bar_data[raw_code]['high'] = row[('high', full_sym)] if ('high', full_sym) in row else bar_data[raw_code]['close']
            bar_data[raw_code]['low'] = row[('low', full_sym)] if ('low', full_sym) in row else bar_data[raw_code]['close']
            if ('month_change', full_sym) in row:
                bar_data[raw_code]['month_change'] = row[('month_change', full_sym)]
        elif ('last_price', full_sym) in row and not pd.isna(row[('last_price', full_sym)]):
            price = row[('last_price', full_sym)]
            bar_data[raw_code]['close'] = price
            bar_data[raw_code]['open'] = price
            bar_data[raw_code]['high'] = price
            bar_data[raw_code]['low'] = price
            bar_data[raw_code]['bid_price_1'] = row[('bid_price_1', full_sym)] if ('bid_price_1', full_sym) in row else price
            bar_data[raw_code]['ask_price_1'] = row[('ask_price_1', full_sym)] if ('ask_price_1', full_sym) in row else price
        else:
            bar_data[raw_code]['close'] = pd.NA

        if 'month_change' not in bar_data[raw_code]:
            bar_data[raw_code]['month_change'] = 0

    return bar_data


def _extract_close_prices(bar_data: dict) -> dict:
    prices = {}
    for sym, data in bar_data.items():
        if data and not pd.isna(data.get('close', pd.NA)):
            prices[sym] = data['close']
    return prices


def _resolve_symbols(symbols_input, data_type):
    is_single = isinstance(symbols_input, str)
    sym_list = [symbols_input] if is_single else symbols_input

    pure_list = []
    full_list = []

    for sym in sym_list:
        query_sym = build_query_symbol(sym, data_type)
        if query_sym is None:
            print(f"[Engine] Warning: 品种 {sym} 未在 config.py 中配置，已跳过。")
            continue

        raw_input = sym.lower()
        month_match = re.match(r"^([a-z]+)(\d+)$", raw_input)
        pure_code = month_match.group(1) if month_match else raw_input

        pure_list.append(pure_code)
        full_list.append(query_sym)

    if not full_list:
        raise ValueError("[Engine] Error: 解析后没有有效的交易品种！")

    if is_single:
        return pure_list[0], full_list[0], full_list
    else:
        return 'multi', f"{len(full_list)} 个品种组合", full_list


def run_backtest(
        strategy_class,
        symbols_input,
        start_date: str,
        end_date: str,
        freq: str,
        data_type: str,
        initial_capital: float = 1000000.0,
        strategy_kwargs: dict = None,
        enable_main_rollover: bool = True,
):
    """
    通用回测执行接口

    换月逻辑 (T日收盘时):
      - 策略在T日正常交易
      - 用昨日收盘价(T-1收盘) 平旧仓（结算真实盈亏）
      - 用T日开盘价开新仓
    """
    if strategy_kwargs is None:
        strategy_kwargs = {}

    strat_sym, target_desc, query_symbols = _resolve_symbols(symbols_input, data_type)

    print("=" * 60)
    print(f"[Engine] 启动任务 | 标的: {target_desc} | 频率: {freq} | 初始资金: ￥{initial_capital:,.2f}")
    print(f"[Engine] 挂载策略: {strategy_class.__name__}")
    print("=" * 60)

    provider = DataProvider()
    print("[Engine] 正在获取历史数据...")

    df = provider.get_history(
        symbols=query_symbols,
        start_date=start_date,
        end_date=end_date,
        freq=freq, data_type=data_type
    )
    if df.empty:
        print("[Engine] 数据获取失败，任务终止。")
        return

    columns_level_1 = list(dict.fromkeys([col[1] for col in df.columns]))

    actual_start = df.index[0]
    actual_end = df.index[-1]
    print(f"[Engine] 数据加载完毕，实际可用区间: {actual_start} 至 {actual_end}")

    account = Account(initial_capital=initial_capital)
    broker = MatchEngine(account=account)
    rollover_handler = MainContractRollover() if enable_main_rollover and MainContractRollover.is_enabled(data_type) else None
    if rollover_handler:
        print("[Engine] 已启用未复权主连换月")
    elif enable_main_rollover and data_type != 'main':
        print(f"[Engine] 换月跳过：data_type='{data_type}' 非未复权主连")

    strategy = strategy_class(broker=broker, account=account, symbol=strat_sym, **strategy_kwargs)
    strategy.on_init()

    print("\n[Engine] 时间轴初始化完成，开始事件驱动模拟...")
    last_date = None
    last_close_prices = {}  # 昨日收盘价，用于换月结算
    equity_records = []
    rollover_count = 0

    for current_time, row in df.iterrows():
        current_date = current_time.date()

        if last_date is not None and current_date != last_date:
            account.settle_daily()

        last_date = current_date

        bar_data = extract_bar_data(row, columns_level_1)

        if strat_sym != 'multi' and pd.isna(bar_data.get(strat_sym, {}).get('close', pd.NA)):
            continue

        # 策略处理当前K线
        broker.process_cross_section(current_time, bar_data)
        strategy.on_bar(current_time, bar_data)

        # 如果是换月K线，执行换月（传入昨收价）
        if rollover_handler:
            count = rollover_handler.process(broker, current_time, bar_data, last_close_prices)
            rollover_count += count

        close_prices = _extract_close_prices(bar_data)
        if close_prices:
            last_close_prices = close_prices  # 更新昨收价
            equity_records.append({
                'datetime': current_time,
                'equity': account.get_total_equity(close_prices),
            })

    print("\n" + "=" * 60)
    print(f"[Engine] 时间轴模拟结束 (模拟至 {actual_end})。")
    if rollover_handler and rollover_count > 0:
        print(f"[Engine] 本次回测共执行 {rollover_count} 次主力换月")
    if broker.pending_orders:
        print(f"⚠️ [Engine] 回测结束仍有 {len(broker.pending_orders)} 笔挂单未撮合")
    account.print_status("最终清算", last_close_prices)
    print("=" * 60)

    if broker.trade_history:
        if 'close' in df.columns.levels[0]:
            price_df = df['close'].copy()
        elif 'last_price' in df.columns.levels[0]:
            price_df = df['last_price'].copy()
        else:
            price_df = pd.DataFrame()

        price_df['datetime'] = price_df.index
        price_df = price_df.reset_index(drop=True)

        equity_df = pd.DataFrame(equity_records) if equity_records else None

        # 计算换月损耗
        rollover_commission = 0.0
        rollover_pnl_loss = 0.0
        for t in broker.trade_history:
            if getattr(t, 'is_rollover', False):
                rollover_commission += t.commission
                if t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]:
                    # 换月平仓盈亏（应该接近0，因为我们按昨收结算）
                    pass

        account_summary = {
            'total_pnl': account.total_pnl,
            'final_equity': account.get_total_equity(last_close_prices) if last_close_prices else account.available + account.frozen_margin,
            'available': account.available,
            'frozen_margin': account.frozen_margin,
            'rollover_count': rollover_count,
            'rollover_commission': rollover_commission,
        }

        # 构建回测参数描述表
        slippage_ticks = 1  # 当前默认滑点为 1 跳
        from config import FEE_DICT, pure_product_code

        if strat_sym.upper() == 'MULTI' and isinstance(symbols_input, list):
            margin_list = []
            for sym in symbols_input:
                p_code = pure_product_code(sym)
                # 💥 增加 .upper() 和 .lower() 双重匹配，彻底解决 TA 等品种查不到报 0 的问题
                meta = FEE_DICT.get(p_code) or FEE_DICT.get(p_code.upper()) or FEE_DICT.get(p_code.lower()) or {}
                rate = meta.get('margin_rate', 0)
                margin_list.append(f"{p_code.upper()}:{rate * 100:.0f}%")
            margin_str = ", ".join(margin_list)
            # 💥 将 MULTI 展开为 MULTI(RB, HC, TA...)
            display_symbol = f"MULTI ({', '.join([s.upper() for s in symbols_input])})"
        else:
            p_code = pure_product_code(strat_sym)
            meta = FEE_DICT.get(p_code) or FEE_DICT.get(p_code.upper()) or FEE_DICT.get(p_code.lower()) or {}
            margin_rate = meta.get('margin_rate', 0.0)
            margin_str = f"{margin_rate * 100:.1f}%"
            display_symbol = strat_sym.upper()

        # 💥 组装最终呈现的字典 (直接删除了 '手续费设置' 这一列)
        describe_params = {
            '数据周期': freq,
            '回测区间': f"{str(start_date).split()[0]} 至 {str(end_date).split()[0]}",
            '初始资金': f"￥{initial_capital:,.2f}",
            '回测品种': display_symbol,
            '滑点设置': f"{slippage_ticks} 跳",
            '保证金率': margin_str
        }


        analyzer = StrategyAnalyzer(
            trades=broker.trade_history,
            price_df=df,
            initial_capital=initial_capital,
            symbol=strat_sym,
            freq=freq,
            strategy_name=strategy_class.__name__,
            account_summary=account_summary,
            equity_df=pd.DataFrame(equity_records),
            describe_params=describe_params
        )
        analyzer.generate_report()

        return analyzer  # 💥 加上这一行！把算好的数据引擎交还给外部！
    else:
        print("[Engine] 回测期间无交易记录产生。")


# 导出 Offset 供其他模块使用
from broker.order import Offset
