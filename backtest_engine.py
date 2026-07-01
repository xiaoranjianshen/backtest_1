# -*- coding: utf-8 -*-
"""
核心调度主循环 (Backtest Engine)
"""
import os
import sys
import inspect
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
from config import build_query_symbol, FEE_DICT, pure_product_code, trade_symbol_code


STRATEGY_CLASS_TO_CONFIG_KEY = {
    "GeneralMultiMAStrategy": "general_multi_ma",
    "BreakoutPyramidStrategy": "breakout_pyramid",
    "DualMAStrategy": "dual_ma",
    "ZScoreReversalStrategy": "zscore_reversal",
    "TickAnomalyScalpingStrategy": "tick_anomaly_scalping",
    "TickRollingBreakoutStrategy": "tick_rolling_breakout",
    "TickVWAPReversionStrategy": "tick_vwap_reversion",
    "UTBotSTCHullStrategy": "utbot_stc_hull",
    "VWAPBandReversionStrategy": "vwap_band_reversion",
    "DonchianATRBreakoutStrategy": "donchian_atr_breakout",
    "OpeningRangeACDStrategy": "opening_range_acd",
    "AmplitudeRankACDStrategy": "amplitude_rank_acd",
    "AmplitudeRankDayBreakoutStrategy": "amplitude_rank_day_breakout",
    "AmplitudeRankDonchianStrategy": "amplitude_rank_donchian",
    "TrendRankDonchianStrategy": "trend_rank_donchian",
    "AbsRetRollingValidationStrategy": "abs_ret_rolling_validation",
    "CompositeFactorStrategy": "composite_factor",
    "CrossMomentumFactor": "cross_momentum",
}


def _row_value(row, field: str, symbol: str, default=pd.NA):
    key = (field, symbol)
    return row[key] if key in row else default


def extract_bar_data(row, columns_level_1):
    bar_data = {}
    for full_sym in columns_level_1:
        match = re.search(r'\((.*?)\)', full_sym)
        raw_code = (match.group(1) if match else str(full_sym).split('.')[-1]).lower()
        if raw_code not in bar_data:
            bar_data[raw_code] = {}

        if ('close', full_sym) in row and not pd.isna(row[('close', full_sym)]):
            bar_data[raw_code]['close'] = row[('close', full_sym)]
            bar_data[raw_code]['open'] = _row_value(row, 'open', full_sym, bar_data[raw_code]['close'])
            bar_data[raw_code]['high'] = _row_value(row, 'high', full_sym, bar_data[raw_code]['close'])
            bar_data[raw_code]['low'] = _row_value(row, 'low', full_sym, bar_data[raw_code]['close'])
            bar_data[raw_code]['volume'] = _row_value(row, 'volume', full_sym, 0.0)
            bar_data[raw_code]['oi'] = _row_value(row, 'oi', full_sym, pd.NA)
            bar_data[raw_code]['is_fresh'] = bool(_row_value(row, 'is_fresh', full_sym, 1.0))
            if ('month_change', full_sym) in row:
                bar_data[raw_code]['month_change'] = row[('month_change', full_sym)]
        elif ('last_price', full_sym) in row and not pd.isna(row[('last_price', full_sym)]):
            price = row[('last_price', full_sym)]
            bid = _row_value(row, 'bid_price_1', full_sym, price)
            ask = _row_value(row, 'ask_price_1', full_sym, price)
            bid_volume = _row_value(row, 'bid_volume_1', full_sym, 0.0)
            ask_volume = _row_value(row, 'ask_volume_1', full_sym, 0.0)
            tick_volume = _row_value(row, 'volume_delta', full_sym, 0.0)
            bar_data[raw_code]['close'] = price
            bar_data[raw_code]['open'] = price
            bar_data[raw_code]['high'] = price
            bar_data[raw_code]['low'] = price
            bar_data[raw_code]['last_price'] = price
            bar_data[raw_code]['bid_price_1'] = bid
            bar_data[raw_code]['ask_price_1'] = ask
            bar_data[raw_code]['bid_volume_1'] = bid_volume
            bar_data[raw_code]['ask_volume_1'] = ask_volume
            bar_data[raw_code]['volume'] = tick_volume
            bar_data[raw_code]['tick_volume'] = tick_volume
            bar_data[raw_code]['cum_volume'] = _row_value(row, 'volume', full_sym, 0.0)
            bar_data[raw_code]['oi'] = _row_value(row, 'oi', full_sym, pd.NA)
            bar_data[raw_code]['is_fresh'] = bool(_row_value(row, 'is_fresh', full_sym, 1.0))
            if not pd.isna(bid) and not pd.isna(ask) and bid > 0 and ask > 0:
                bar_data[raw_code]['mid_price'] = (float(bid) + float(ask)) / 2.0
                bar_data[raw_code]['spread'] = float(ask) - float(bid)
            else:
                bar_data[raw_code]['mid_price'] = price
                bar_data[raw_code]['spread'] = 0.0
        else:
            bar_data[raw_code]['close'] = pd.NA

        if 'month_change' not in bar_data[raw_code]:
            bar_data[raw_code]['month_change'] = 0

    return bar_data


def _tuple_value(row_values, col_pos: dict, field: str, symbol: str, default=pd.NA):
    pos = col_pos.get((field, symbol))
    return row_values[pos] if pos is not None else default


def extract_tick_bar_data_from_tuple(row_values, columns_level_1, col_pos: dict):
    """Build tick bar_data from an itertuples row without changing event order."""
    bar_data = {}
    for full_sym in columns_level_1:
        match = re.search(r'\((.*?)\)', full_sym)
        raw_code = (match.group(1) if match else str(full_sym).split('.')[-1]).lower()
        price = _tuple_value(row_values, col_pos, 'last_price', full_sym, pd.NA)
        if price is None or pd.isna(price):
            bar_data[raw_code] = {'close': pd.NA, 'month_change': 0}
            continue

        bid = _tuple_value(row_values, col_pos, 'bid_price_1', full_sym, price)
        ask = _tuple_value(row_values, col_pos, 'ask_price_1', full_sym, price)
        bid_volume = _tuple_value(row_values, col_pos, 'bid_volume_1', full_sym, 0.0)
        ask_volume = _tuple_value(row_values, col_pos, 'ask_volume_1', full_sym, 0.0)
        tick_volume = _tuple_value(row_values, col_pos, 'volume_delta', full_sym, 0.0)

        bar = {
            'close': price,
            'open': price,
            'high': price,
            'low': price,
            'last_price': price,
            'bid_price_1': bid,
            'ask_price_1': ask,
            'bid_volume_1': bid_volume,
            'ask_volume_1': ask_volume,
            'volume': tick_volume,
            'tick_volume': tick_volume,
            'cum_volume': _tuple_value(row_values, col_pos, 'volume', full_sym, 0.0),
            'oi': _tuple_value(row_values, col_pos, 'oi', full_sym, pd.NA),
            'is_fresh': bool(_tuple_value(row_values, col_pos, 'is_fresh', full_sym, 1.0)),
            'month_change': 0,
        }
        if not pd.isna(bid) and not pd.isna(ask) and bid > 0 and ask > 0:
            bar['mid_price'] = (float(bid) + float(ask)) / 2.0
            bar['spread'] = float(ask) - float(bid)
        else:
            bar['mid_price'] = price
            bar['spread'] = 0.0
        bar_data[raw_code] = bar

    return bar_data


def _extract_close_prices(bar_data: dict) -> dict:
    prices = {}
    for sym, data in bar_data.items():
        if data and not pd.isna(data.get('close', pd.NA)):
            prices[sym] = data['close']
    return prices


def _calc_position_notional(account: Account, current_prices: dict) -> float:
    """Calculate absolute notional value of all open positions at current close prices."""
    total_notional = 0.0
    for pos_key, pos in account.positions.items():
        symbol, _ = pos_key.rsplit('_', 1)
        price = current_prices.get(symbol)
        volume = account._position_volume(pos)
        if price is None or pd.isna(price) or volume <= 0:
            continue
        meta = account.fee_model._get_meta_data(symbol)
        total_notional += abs(float(price) * volume * meta['multiplier'])
    return total_notional


def _resolve_symbols(symbols_input, data_type):
    is_single = isinstance(symbols_input, str)
    sym_list = [symbols_input] if is_single else symbols_input

    pure_list = []
    full_list = []

    for sym in sym_list:
        query_sym = build_query_symbol(sym, data_type)
        if query_sym is None:
            print(f"[Engine Warning] 品种 {sym} 未在 config.py 中配置，已跳过。")
            continue

        raw_input = str(sym).lower()
        month_match = re.match(r"^([a-z]+)(\d+)$", raw_input)
        pure_code = trade_symbol_code(query_sym) if data_type == 'all' and month_match else (
            month_match.group(1) if month_match else raw_input
        )

        pure_list.append(pure_code)
        full_list.append(query_sym)

    if not full_list:
        raise ValueError("[Engine] Error: 解析后没有有效的交易品种！")

    if is_single:
        return pure_list[0], full_list[0], full_list, pure_list
    return 'multi', f"{len(full_list)} 个品种组合", full_list, pure_list


def _symbols_as_list(symbols_input) -> list[str]:
    if isinstance(symbols_input, str):
        return [symbols_input]
    return list(symbols_input or [])


def _strategy_key(strategy_class) -> str:
    return STRATEGY_CLASS_TO_CONFIG_KEY.get(strategy_class.__name__, strategy_class.__name__)


def _extract_symbols_from_columns(columns_level_1) -> set[str]:
    available = set()
    for full_sym in columns_level_1:
        match = re.search(r'\((.*?)\)', str(full_sym))
        raw = match.group(1) if match else str(full_sym)
        available.add(pure_product_code(raw))
        available.add(trade_symbol_code(raw))
    return available


def _requested_symbol_available(symbol: str, available_symbols: set[str]) -> bool:
    raw = str(symbol).lower()
    if re.match(r"^[a-z]+\d+$", raw):
        return trade_symbol_code(raw) in available_symbols
    return pure_product_code(raw) in available_symbols


def _maybe_add_trading_calendar(strategy_class, strategy_kwargs: dict, trading_index) -> dict:
    try:
        parameters = inspect.signature(strategy_class.__init__).parameters
    except (TypeError, ValueError):
        return strategy_kwargs

    if "trading_calendar" not in parameters or "trading_calendar" in strategy_kwargs:
        return strategy_kwargs

    updated = dict(strategy_kwargs)
    updated["trading_calendar"] = [pd.Timestamp(value).normalize() for value in trading_index]
    return updated


def _build_run_config(
        strategy_class,
        symbols_input,
        start_date,
        end_date,
        freq,
        data_type,
        initial_capital,
        strategy_kwargs,
        enable_main_rollover,
):
    config = {
        "strategy": _strategy_key(strategy_class),
        "symbols": _symbols_as_list(symbols_input),
        "start_date": str(start_date).split()[0],
        "end_date": str(end_date).split()[0],
        "freq": freq,
        "data_type": data_type,
        "initial_capital": float(initial_capital),
        "enable_main_rollover": bool(enable_main_rollover),
    }

    if isinstance(strategy_kwargs, dict):
        if "fast_window" in strategy_kwargs:
            config["fast_window"] = strategy_kwargs["fast_window"]
        if "slow_window" in strategy_kwargs:
            config["slow_window"] = strategy_kwargs["slow_window"]

        for key in (
            "scalp_mode",
            "shock_window_seconds",
            "lookback_days",
            "tail_prob",
            "min_move_bps",
            "min_history_samples",
            "directional_ratio",
            "max_spread_ticks",
            "hold_seconds",
            "take_profit_ticks",
            "stop_loss_ticks",
            "cooldown_seconds",
            "threshold_refresh_ticks",
            "pause_seconds",
            "reversal_confirm_seconds",
            "reversal_retrace_ratio",
            "reversal_min_retrace_ticks",
            "require_history_ready",
            "warmup_days",
            "hma_length",
            "atr_period",
            "ut_key_value",
            "stc_length",
            "stc_fast",
            "stc_slow",
            "stc_factor",
            "stc_long_max",
            "stc_short_min",
            "require_price_above_hull",
            "exit_on_opposite_signal",
            "max_hold_bars",
            "cooldown_bars",
            "avoid_session_close_seconds",
            "max_entries_per_symbol_per_day",
            "exit_order_type",
            "exit_order_ttl_seconds",
            "std_window",
            "entry_z",
            "exit_z",
            "min_bars_in_session",
            "min_std_ticks",
            "max_vwap_slope_ticks",
            "slope_window",
            "session_start_hour",
            "donchian_window",
            "breakout_buffer_ticks",
            "min_channel_atr",
            "max_extension_atr",
            "atr_stop_mult",
            "trend_window",
            "exit_on_midline",
            "allowed_entry_hours",
            "signal_path",
            "validation_path",
            "monthly_validation_path",
            "model_name",
            "min_validation_hit_rate",
            "validation_mode",
            "validation_lookback_months",
            "min_validation_rows",
            "signal_time_column",
            "signal_frequency",
            "daily_signal_policy",
            "edge_quantile",
            "edge_threshold_mode",
            "edge_threshold_lookback",
            "min_threshold_history",
            "max_total_margin_pct",
            "max_positions",
            "one_contract_per_product",
            "close_on_failed_signal",
        ):
            if key in strategy_kwargs:
                config[key] = strategy_kwargs[key]

        sizing = strategy_kwargs.get("sizing") or {}
        if isinstance(sizing, dict):
            config.update({
                "sizing_mode": sizing.get("mode"),
                "sizing_value": sizing.get("value"),
                "min_volume": sizing.get("min_volume"),
                "max_volume": sizing.get("max_volume"),
                "round_lot": sizing.get("round_lot", 1),
            })

        execution = strategy_kwargs.get("execution") or {}
        if isinstance(execution, dict):
            config.update({
                "order_type": execution.get("order_type"),
                "price_field": execution.get("price_field", "close"),
                "slippage_ticks": execution.get("slippage_ticks"),
                "limit_mode": execution.get("limit_mode"),
                "limit_ticks": execution.get("ticks"),
            })

        exit_config = strategy_kwargs.get("exit") or {}
        if isinstance(exit_config, dict):
            config.update({
                "close_pct": exit_config.get("close_pct"),
                "allow_reverse": exit_config.get("allow_reverse"),
                "respect_pending_orders": exit_config.get("respect_pending_orders"),
            })

    return {key: value for key, value in config.items() if value is not None}


def _execution_attr(config, key, default=None):
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _describe_slippage_setting(strategy, strategy_kwargs: dict) -> str:
    execution_config = getattr(strategy, 'execution_config', None)
    if execution_config is None:
        execution_config = (strategy_kwargs or {}).get('execution', {})

    order_type = str(_execution_attr(execution_config, 'order_type', 'market')).lower()
    slippage_ticks = float(_execution_attr(execution_config, 'slippage_ticks', 1.0))
    slippage_text = f"{slippage_ticks:g} 跳"

    if order_type == 'limit':
        return f"策略限价单: 0 跳；市价/换月默认: {slippage_text}"
    if order_type == 'opponent':
        return f"策略对价单: 吃对手盘，0 跳；市价/换月默认: {slippage_text}"
    return f"策略市价单: {slippage_text}"


def _describe_margin_rates(symbols) -> str:
    seen_products = set()
    margin_items = []
    for sym in symbols:
        p_code = pure_product_code(sym)
        product_key = p_code.lower()
        if product_key in seen_products:
            continue
        seen_products.add(product_key)
        meta = FEE_DICT.get(p_code) or FEE_DICT.get(p_code.upper()) or FEE_DICT.get(p_code.lower()) or {}
        rate = float(meta.get('margin_rate', 0.0) or 0.0)
        margin_items.append(f"{p_code.upper()}:{rate * 100:.0f}%")
    return ", ".join(margin_items)


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

    strat_sym, target_desc, query_symbols, requested_symbols = _resolve_symbols(symbols_input, data_type)

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
    available_symbols = _extract_symbols_from_columns(columns_level_1)
    missing_symbols = [sym for sym in requested_symbols if not _requested_symbol_available(sym, available_symbols)]
    if missing_symbols:
        print(
            "[Engine Warning] 以下请求品种在实际数据矩阵中没有有效数据，已无法参与本次回测: "
            f"{', '.join([sym.upper() for sym in missing_symbols])}"
        )
        print("[Engine Warning] 请检查对应数据表是否已有该品种主连/指数数据，或数据库 symbol 命名是否与 config.py 一致。")

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

    strategy_kwargs = _maybe_add_trading_calendar(strategy_class, strategy_kwargs, df.index)
    strategy = strategy_class(broker=broker, account=account, symbol=strat_sym, **strategy_kwargs)
    strategy.on_init()

    print("\n[Engine] 时间轴初始化完成，开始事件驱动模拟...")
    last_date = None
    last_close_prices = {}  # 昨日收盘价，用于换月结算
    equity_records = []
    rollover_count = 0

    is_tick_matrix = freq == 'tick' and 'last_price' in df.columns.levels[0]
    if is_tick_matrix:
        col_pos = {col: pos for pos, col in enumerate(df.columns)}
        for row_tuple in df.itertuples(index=True, name=None):
            current_time = row_tuple[0]
            row_values = row_tuple[1:]
            current_date = current_time.date()

            if last_date is not None and current_date != last_date:
                account.settle_daily(last_close_prices)

            last_date = current_date
            bar_data = extract_tick_bar_data_from_tuple(row_values, columns_level_1, col_pos)

            if strat_sym != 'multi' and pd.isna(bar_data.get(strat_sym, {}).get('close', pd.NA)):
                continue

            # Tick fast path preserves the original event order: match previous
            # orders first, then let the strategy read the current tick.
            if rollover_handler:
                count = rollover_handler.process(broker, current_time, bar_data, last_close_prices)
                rollover_count += count

            broker.process_cross_section(current_time, bar_data)
            strategy.on_bar(current_time, bar_data)

            close_prices = _extract_close_prices(bar_data)
            if close_prices:
                last_close_prices = close_prices
                equity_records.append({
                    'datetime': current_time,
                    'equity': account.get_total_equity(close_prices),
                    'position_notional': _calc_position_notional(account, close_prices),
                })
    else:
        for current_time, row in df.iterrows():
            current_date = current_time.date()

            if last_date is not None and current_date != last_date:
                account.settle_daily(last_close_prices)

            last_date = current_date

            bar_data = extract_bar_data(row, columns_level_1)

            if strat_sym != 'multi' and pd.isna(bar_data.get(strat_sym, {}).get('close', pd.NA)):
                continue

            # 如果是换月K线，执行换月（传入昨收价）
            if rollover_handler:
                count = rollover_handler.process(broker, current_time, bar_data, last_close_prices)
                rollover_count += count

            # 事件顺序：先用当前 bar 撮合上一根 bar 留下的挂单，再让策略读取当前 bar 生成新订单。
            # 因此策略在 on_bar 里发出的订单最早会在下一根 bar 被撮合。
            broker.process_cross_section(current_time, bar_data)
            strategy.on_bar(current_time, bar_data)

            close_prices = _extract_close_prices(bar_data)
            if close_prices:
                last_close_prices = close_prices  # 更新昨收价
                equity_records.append({
                    'datetime': current_time,
                    'equity': account.get_total_equity(close_prices),
                    'position_notional': _calc_position_notional(account, close_prices),
                })

    print("\n" + "=" * 60)
    print(f"[Engine] 时间轴模拟结束 (模拟至 {actual_end})。")
    if rollover_handler and rollover_count > 0:
        print(f"[Engine] 本次回测共执行 {rollover_count} 次主力换月")
    if broker.pending_orders:
        print(f"[Engine Warning] 回测结束仍有 {len(broker.pending_orders)} 笔挂单未撮合")
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

        rollover_commission = 0.0
        for t in broker.trade_history:
            if getattr(t, 'is_rollover', False):
                rollover_commission += t.commission

        account_summary = {
            'total_pnl': account.total_pnl,
            'final_equity': account.get_total_equity(last_close_prices) if last_close_prices else account.available + account.frozen_margin,
            'available': account.available,
            'frozen_margin': account.frozen_margin,
            'rollover_count': rollover_count,
            'rollover_commission': rollover_commission,
        }

        # 构建回测参数描述表
        if strat_sym.upper() == 'MULTI' and isinstance(symbols_input, list):
            margin_str = _describe_margin_rates(symbols_input)
            display_symbol = f"MULTI ({', '.join([s.upper() for s in symbols_input])})"
        else:
            p_code = pure_product_code(strat_sym)
            meta = FEE_DICT.get(p_code) or FEE_DICT.get(p_code.upper()) or FEE_DICT.get(p_code.lower()) or {}
            margin_rate = meta.get('margin_rate', 0.0)
            margin_str = f"{margin_rate * 100:.1f}%"
            display_symbol = strat_sym.upper()

        # 报告参数只放用户需要核对的回测口径；手续费明细在绩效表中按品种展示。
        describe_params = {
            '数据周期': freq,
            '回测区间': f"{str(start_date).split()[0]} 至 {str(end_date).split()[0]}",
            '初始资金': f"￥{initial_capital:,.2f}",
            '回测品种': display_symbol,
            '滑点设置': _describe_slippage_setting(strategy, strategy_kwargs),
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
            describe_params=describe_params,
            signal_records=getattr(strategy, 'raw_signal_records', []),
            rebalance_records=getattr(strategy, 'signal_records', []),
        )
        analyzer.run_config = _build_run_config(
            strategy_class=strategy_class,
            symbols_input=symbols_input,
            start_date=start_date,
            end_date=end_date,
            freq=freq,
            data_type=data_type,
            initial_capital=initial_capital,
            strategy_kwargs=strategy_kwargs,
            enable_main_rollover=enable_main_rollover,
        )
        analyzer.generate_report()

        return analyzer
    else:
        print("[Engine] 回测期间无交易记录产生。")


# 导出 Offset 供其他模块使用
from broker.order import Offset
