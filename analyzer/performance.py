# -*- coding: utf-8 -*-
"""
\u5de5\u4e1a\u7ea7\u56de\u6d4b\u5f15\u64ce - \u5206\u6790\u5e08\u6a21\u5757 (Analyzer)
\u529f\u80fd\uff1aFIFO \u4ea4\u6613\u914d\u5bf9\u3001\u91cf\u5316\u6307\u6807\u8ba1\u7b97\u3001\u8d44\u91d1/\u4ef7\u683c\u5bf9\u6bd4\u56fe\u3001\u591a\u54c1\u79cd\u5206\u6790\u3001\u4fe1\u53f7\u68c0\u6d4b\u3002
"""
import os
import sys
import re
import json
from collections import defaultdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from broker.order import Direction, Offset


PRICE_FIELD_PRIORITY = ('close', 'last_price', 'settlement', 'open')
SECTOR_ORDER = [
    "\u9ed1\u8272",
    "\u6709\u8272",
    "\u8d35\u91d1\u5c5e",
    "\u5316\u5de5",
    "\u80fd\u6e90",
    "\u6cb9\u8102\u6cb9\u6599",
    "\u8f6f\u5546\u54c1",
    "\u751f\u9c9c",
    "\u5efa\u6750",
    "\u80a1\u6307",
    "\u56fd\u503a",
    "\u822a\u8fd0",
    "\u65b0\u80fd\u6e90",
    "\u672a\u5206\u7c7b",
]


def _console_text(value) -> str:
    text = str(value).replace('\u00a5', '\uffe5')
    encoding = sys.stdout.encoding or 'utf-8'
    return text.encode(encoding, errors='replace').decode(encoding, errors='replace')


def _safe_print(value=""):
    print(_console_text(value))


def _lookup_multiplier(symbol: str) -> float:
    from config import FEE_DICT, pure_product_code
    raw_code = pure_product_code(symbol)
    meta = FEE_DICT.get(raw_code) or FEE_DICT.get(raw_code.upper()) or FEE_DICT.get(raw_code.lower())
    return meta['multiplier'] if meta else 10.0


def _lookup_meta(symbol: str) -> dict:
    from config import FEE_DICT, pure_product_code
    raw_code = pure_product_code(symbol)
    return FEE_DICT.get(raw_code) or FEE_DICT.get(raw_code.upper()) or FEE_DICT.get(raw_code.lower()) or {}


def _extract_product_code_from_label(label) -> str:
    """Extract product code from raw or cleaned symbol labels."""
    from config import pure_product_code

    label_str = str(label)
    paren_match = re.search(r'\(([a-zA-Z]+[0-9]*)\)', label_str)
    raw = paren_match.group(1) if paren_match else label_str
    return pure_product_code(raw)


def _symbol_price_lookup_keys(label) -> list[str]:
    """Build possible lookup keys for price columns and trade symbols."""
    from config import pure_product_code, trade_symbol_code

    keys: list[str] = []
    label_str = str(label).strip()
    if label_str:
        keys.append(label_str.lower())

    paren_match = re.search(r'\((.*?)\)', label_str)
    if paren_match:
        inner_label = paren_match.group(1)
        keys.append(inner_label.lower())
        try:
            inner_trade_code = trade_symbol_code(inner_label)
            if inner_trade_code:
                keys.append(str(inner_trade_code).lower())
        except Exception:
            pass

    if not paren_match:
        try:
            trade_code = trade_symbol_code(label_str)
            if trade_code:
                keys.append(str(trade_code).lower())
        except Exception:
            pass

    try:
        product_code = pure_product_code(label_str)
        if product_code:
            keys.append(str(product_code).lower())
    except Exception:
        pass

    return list(dict.fromkeys(keys))


def _symbol_sector(symbol: str) -> str:
    from config import SYMBOL_DICT, pure_product_code

    raw_code = pure_product_code(symbol)
    for code, attrs in SYMBOL_DICT.items():
        if code.lower() == raw_code.lower():
            return attrs[3] if len(attrs) > 3 else "\u672a\u5206\u7c7b"
    return "\u672a\u5206\u7c7b"


def _symbol_sort_key(symbol: str):
    from config import SYMBOL_DICT, pure_product_code

    raw_code = pure_product_code(symbol)
    sector = _symbol_sector(raw_code)
    sector_rank = SECTOR_ORDER.index(sector) if sector in SECTOR_ORDER else len(SECTOR_ORDER)
    symbol_rank = {code.lower(): idx for idx, code in enumerate(SYMBOL_DICT)}.get(raw_code.lower(), 9999)
    return sector_rank, symbol_rank, raw_code.lower()


def _interpolate_hex_color(start_hex: str, end_hex: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, float(ratio)))
    start = tuple(int(start_hex[i:i + 2], 16) for i in (1, 3, 5))
    end = tuple(int(end_hex[i:i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(start[idx] + (end[idx] - start[idx]) * ratio) for idx in range(3))
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _pnl_to_color(pnl: float, max_abs_pnl: float) -> str:
    if max_abs_pnl <= 0:
        return "#e5e7eb"
    intensity = min(1.0, abs(float(pnl)) / max_abs_pnl)
    if pnl > 0:
        return _interpolate_hex_color("#fecaca", "#dc2626", intensity)
    if pnl < 0:
        return _interpolate_hex_color("#bbf7d0", "#16a34a", intensity)
    return "#e5e7eb"


def _trading_time_rangebreaks():
    """
    Compress common Chinese futures non-trading intervals on intraday charts.

    This changes only the Plotly display axis. It does not remove rows from
    equity, trade, or exported CSV data.
    """
    return [
        dict(bounds=["sat", "mon"]),
        dict(pattern="hour", bounds=[2.5, 9.0]),
        dict(pattern="hour", bounds=[10.25, 10.5]),
        dict(pattern="hour", bounds=[11.5, 13.5]),
        dict(pattern="hour", bounds=[15.0, 21.0]),
    ]


def _uses_intraday_axis(values) -> bool:
    if values is None or len(values) == 0:
        return False
    dt = pd.to_datetime(pd.Series(values), errors='coerce').dropna()
    if dt.empty:
        return False
    return bool((dt.dt.time != pd.Timestamp("00:00:00").time()).any())


def _time_axis_layout(values=None) -> dict:
    layout = dict(type="date")
    if _uses_intraday_axis(values):
        layout["rangebreaks"] = _trading_time_rangebreaks()
    return layout


def _trading_session_dates(values, freq: str) -> pd.Series:
    """Return a stable session key for daily metric aggregation.

    Chinese futures night trading crosses midnight. For intraday data, moving
    the session boundary to 09:00 keeps the night segment and the following
    early-morning segment in one analytical day. Daily bars already carry a
    trading date, so their calendar date must remain unchanged.
    """
    source = values if isinstance(values, pd.Series) else pd.Series(values)
    datetimes = pd.to_datetime(source, errors="coerce")
    if str(freq).lower() in {"1d", "d", "day", "daily"}:
        return datetimes.dt.normalize()
    return (datetimes - pd.Timedelta(hours=9)).dt.normalize()


REPORT_OVERVIEW_MARGIN = dict(l=72, r=72, t=16, b=54)
REPORT_DAILY_RESOLUTION_MIN_DAYS = 365
REPORT_DAILY_RESOLUTION_MIN_POINTS = 100_000
INTRADAY_REPORT_FREQS = {"tick", "1m", "3m", "5m", "15m", "30m", "60m", "1h"}


def _format_time_label(value, tick_label: bool = False) -> str:
    ts = pd.to_datetime(value, errors='coerce')
    if pd.isna(ts):
        return ""
    if tick_label:
        if ts.hour == 0 and ts.minute == 0 and ts.second == 0 and ts.microsecond == 0:
            return ts.strftime("%Y-%m-%d")
        return ts.strftime("%m-%d<br>%H:%M:%S")
    if ts.microsecond:
        return ts.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
        return ts.strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _build_continuous_time_axis(base_values, max_ticks: int = 8) -> dict:
    """
    Use a dense integer axis for intraday charts while showing real timestamps.

    Plotly date axes preserve calendar distance, so nights and weekends either
    create blank space or need rangebreaks. A dense axis keeps the chart
    continuous; tick labels and hover text still expose the original timestamp.
    """
    if base_values is None or len(base_values) == 0 or not _uses_intraday_axis(base_values):
        return {
            "enabled": False,
            "layout": _time_axis_layout(base_values),
            "base_ns": None,
        }

    dt = pd.to_datetime(pd.Series(base_values), errors='coerce')
    n = len(dt)
    if n == 0:
        return {"enabled": False, "layout": dict(type="date"), "base_ns": None}

    tick_count = max(2, min(max_ticks, n))
    tick_idx = np.linspace(0, n - 1, tick_count, dtype=int).tolist()
    tick_idx = sorted(set(tick_idx))

    return {
        "enabled": True,
        "base_ns": dt.astype('int64').to_numpy(),
        "layout": dict(
            type="linear",
            tickmode="array",
            tickvals=tick_idx,
            ticktext=[_format_time_label(dt.iloc[i], tick_label=True) for i in tick_idx],
            showgrid=True,
            gridcolor='#f3f4f6',
        ),
    }


def _map_to_continuous_time_axis(values, axis: dict):
    if values is None:
        return [], []
    labels = [_format_time_label(value) for value in values]
    if not axis.get("enabled"):
        return values, labels

    base_ns = axis.get("base_ns")
    if base_ns is None or len(base_ns) == 0:
        return list(range(len(values))), labels

    value_ns = pd.to_datetime(pd.Series(values), errors='coerce').astype('int64').to_numpy()
    positions = []
    invalid_ns = np.iinfo(np.int64).min
    for item in value_ns:
        if item == invalid_ns:
            positions.append(None)
            continue
        pos = int(np.searchsorted(base_ns, item, side='left'))
        if pos <= 0:
            positions.append(0)
            continue
        if pos >= len(base_ns):
            positions.append(len(base_ns) - 1)
            continue
        left = pos - 1
        positions.append(left if abs(item - base_ns[left]) <= abs(base_ns[pos] - item) else pos)
    return positions, labels


def _price_field_from_column(col) -> str:
    if isinstance(col, tuple) and len(col) >= 2:
        return str(col[0]).lower()
    return 'close'


def _symbol_label_from_column(col) -> str:
    if isinstance(col, tuple) and len(col) >= 2:
        return str(col[1])
    return str(col)


def _price_field_label(field: str) -> str:
    labels = {
        'close': '\u6536\u76d8\u4ef7',
        'last_price': '\u6700\u65b0\u4ef7',
        'settlement': '\u7ed3\u7b97\u4ef7',
        'open': '\u5f00\u76d8\u4ef7',
    }
    return labels.get(field, field)


def _annualize_return(final_value: float, initial_value: float, days: int) -> float:
    if initial_value <= 0 or final_value <= 0:
        return -1.0
    return (final_value / initial_value) ** (365 / max(days, 1)) - 1


class StrategyAnalyzer:
    def __init__(self, trades: list, price_df: pd.DataFrame, initial_capital: float,
                 symbol: str, freq: str, strategy_name: str,
                 account_summary: dict = None, equity_df: pd.DataFrame = None,
                 describe_params: dict = None, signal_records: list | None = None,
                 rebalance_records: list | None = None,
                 selection_records: list | None = None):
        self.trades = trades
        self.price_df = price_df.copy()
        self.initial_capital = initial_capital
        self.symbol = symbol.upper()
        self.freq = freq.lower()
        self.strategy_name = strategy_name
        self.account_summary = account_summary or {}
        self.equity_df = equity_df.copy() if equity_df is not None else None
        self.describe_params = describe_params or {}
        self.signal_records = list(signal_records or [])
        self.rebalance_records = list(rebalance_records or [])
        self.selection_records = list(selection_records or [])
        self.signal_df = pd.DataFrame()

        self.matched_trades = []
        self.metrics = {}
        self.metrics_list = []
        self.unmatched_close_volume = 0

        self.output_dir = os.path.join(PROJECT_ROOT, 'analyzer',
                                       f"{self.symbol}_{self.freq}_{self.strategy_name}_Backtest")
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def _match_trades_fifo(self):
        """FIFO �?平仓配�?：按品�?�?��队列，换月流水不参与配�?"""
        self.matched_trades = []
        self.unmatched_close_volume = 0
        long_queues = defaultdict(list)
        short_queues = defaultdict(list)

        for t in self.trades:
            sym = t.symbol
            is_close = t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]

            if not is_close:
                queue_item = {
                    'trade': t,
                    'remaining_volume': t.volume,
                    'original_volume': t.volume,
                }
                if t.direction == Direction.LONG:
                    long_queues[sym].append(queue_item)
                else:
                    short_queues[sym].append(queue_item)
                continue

            remain_vol = t.volume
            close_original_volume = t.volume
            if t.direction == Direction.SHORT:
                queue = long_queues[sym]
                direction_label = 'Long'
            else:
                queue = short_queues[sym]
                direction_label = 'Short'

            while remain_vol > 0 and queue:
                queue_item = queue[0]
                target = queue_item['trade']
                match_vol = min(remain_vol, queue_item['remaining_volume'])
                mult = _lookup_multiplier(sym)

                if direction_label == 'Long':
                    gross_pnl = (t.price - target.price) * match_vol * mult
                else:
                    gross_pnl = (target.price - t.price) * match_vol * mult

                open_comm = target.commission * (match_vol / (queue_item['original_volume'] + 1e-9))
                close_comm = t.commission * (match_vol / (close_original_volume + 1e-9))
                net_pnl = gross_pnl - open_comm - close_comm

                self.matched_trades.append({
                    'symbol': sym,
                    'open_time': target.trade_time, 'open_price': target.price,
                    'close_time': t.trade_time, 'close_price': t.price,
                    'direction': direction_label, 'volume': match_vol,
                    'gross_pnl': gross_pnl, 'net_pnl': net_pnl, 'commission': open_comm + close_comm,
                    'is_rollover': bool(getattr(target, 'is_rollover', False) or getattr(t, 'is_rollover', False)),
                })

                queue_item['remaining_volume'] -= match_vol
                remain_vol -= match_vol
                if queue_item['remaining_volume'] <= 0:
                    queue.pop(0)

            self.unmatched_close_volume += remain_vol

        self.match_df = pd.DataFrame(self.matched_trades)
        if not self.match_df.empty:
            self.match_df['hold_time_hours'] = (
                self.match_df['close_time'] - self.match_df['open_time']
            ).dt.total_seconds() / 3600.0

    def _calculate_metrics(self):
        """计算绩效指标 (�?��多品种按行展�?)"""
        self.metrics_list = []

        # 1. 计算总指�?
        total_metrics = self._calc_single_metrics(self.match_df, self.symbol, self.strategy_name, is_total=True)
        self.metrics_list.append(total_metrics)

        # 2. 计算各分品�?明细
        if self.symbol == 'MULTI' and not self.match_df.empty:
            for sym, df_sym in self.match_df.groupby('symbol'):
                sym_metrics = self._calc_single_metrics(df_sym, sym, "-", is_total=False)
                self.metrics_list.append(sym_metrics)

        self.metrics = self.metrics_list[0]

    def _get_last_price_by_symbol(self) -> dict:
        """Return last available price by product code from the analyzer price matrix."""
        if hasattr(self, '_last_price_by_symbol_cache'):
            return self._last_price_by_symbol_cache

        prices = {}
        if getattr(self, 'price_df', None) is None or self.price_df.empty:
            self._last_price_by_symbol_cache = prices
            return prices

        for col in self.price_df.columns:
            if col == 'datetime':
                continue

            field = _price_field_from_column(col)
            if field not in PRICE_FIELD_PRIORITY:
                continue

            code = _extract_product_code_from_label(_symbol_label_from_column(col))
            series = pd.to_numeric(self.price_df[col], errors='coerce').dropna()
            if series.empty:
                continue

            priority = PRICE_FIELD_PRIORITY.index(field)
            last_price = float(series.iloc[-1])
            if code not in prices or priority < prices[code][0]:
                prices[code] = (priority, last_price)
            for key in _symbol_price_lookup_keys(_symbol_label_from_column(col)):
                if key not in prices or priority < prices[key][0]:
                    prices[key] = (priority, last_price)

        self._last_price_by_symbol_cache = {code: item[1] for code, item in prices.items()}
        return self._last_price_by_symbol_cache

    def _get_open_mtm_pnl_by_symbol(self) -> dict:
        """Estimate open-position PnL minus remaining open commission by symbol."""
        if hasattr(self, '_open_mtm_pnl_by_symbol_cache'):
            return self._open_mtm_pnl_by_symbol_cache

        long_queues = defaultdict(list)
        short_queues = defaultdict(list)

        for t in sorted(self.trades, key=lambda trade: trade.trade_time):
            sym = t.symbol.lower()
            is_close = t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]

            if not is_close:
                item = {
                    'price': float(t.price),
                    'remaining_volume': int(t.volume),
                    'original_volume': int(t.volume),
                    'commission': float(t.commission),
                }
                if t.direction == Direction.LONG:
                    long_queues[sym].append(item)
                else:
                    short_queues[sym].append(item)
                continue

            remain_vol = int(t.volume)
            queue = long_queues[sym] if t.direction == Direction.SHORT else short_queues[sym]
            while remain_vol > 0 and queue:
                item = queue[0]
                match_vol = min(remain_vol, item['remaining_volume'])
                item['remaining_volume'] -= match_vol
                remain_vol -= match_vol
                if item['remaining_volume'] <= 0:
                    queue.pop(0)

        last_prices = self._get_last_price_by_symbol()
        mtm = defaultdict(float)

        for sym, queue in long_queues.items():
            last_price = last_prices.get(sym)
            if last_price is None:
                continue
            multiplier = _lookup_multiplier(sym)
            for item in queue:
                vol = item['remaining_volume']
                if vol <= 0:
                    continue
                open_commission = item['commission'] * (vol / (item['original_volume'] + 1e-9))
                mtm[sym] += (last_price - item['price']) * vol * multiplier - open_commission

        for sym, queue in short_queues.items():
            last_price = last_prices.get(sym)
            if last_price is None:
                continue
            multiplier = _lookup_multiplier(sym)
            for item in queue:
                vol = item['remaining_volume']
                if vol <= 0:
                    continue
                open_commission = item['commission'] * (vol / (item['original_volume'] + 1e-9))
                mtm[sym] += (item['price'] - last_price) * vol * multiplier - open_commission

        self._open_mtm_pnl_by_symbol_cache = dict(mtm)
        return self._open_mtm_pnl_by_symbol_cache

    def _get_commission_events(self, sym_name: str | None = None) -> pd.DataFrame:
        """
        Actual charged commissions from broker trades.

        `match_df` only contains FIFO-matched closed trades, so it excludes open
        position commissions and rollover costs. Anything named "\u7d2f\u8ba1\u624b\u7eed\u8d39" should
        come from the physical trade ledger instead.
        """
        rows = []
        target_sym = sym_name.lower() if sym_name else None
        for trade in self.trades:
            trade_sym = trade.symbol.lower()
            if target_sym is not None and trade_sym != target_sym:
                continue
            rows.append({
                'datetime': pd.to_datetime(trade.trade_time),
                'symbol': trade_sym,
                'commission': float(trade.commission),
            })
        if not rows:
            return pd.DataFrame(columns=['datetime', 'symbol', 'commission'])
        return pd.DataFrame(rows).sort_values('datetime')

    def _get_turnover_events(self, sym_name: str | None = None) -> pd.DataFrame:
        """Actual trade notional by fill, including open trades, closes, and rollovers."""
        rows = []
        target_sym = sym_name.lower() if sym_name else None
        for trade in self.trades:
            trade_sym = trade.symbol.lower()
            if target_sym is not None and trade_sym != target_sym:
                continue
            multiplier = _lookup_multiplier(trade_sym)
            rows.append({
                'datetime': pd.to_datetime(trade.trade_time),
                'symbol': trade_sym,
                'turnover': abs(float(trade.price) * int(trade.volume) * multiplier),
            })
        if not rows:
            return pd.DataFrame(columns=['datetime', 'symbol', 'turnover'])
        return pd.DataFrame(rows).sort_values('datetime')

    def _get_symbol_total_pnl(self) -> dict:
        """Realized FIFO net PnL plus open-position mark-to-market PnL by symbol."""
        pnl_by_symbol = defaultdict(float)
        if getattr(self, 'match_df', None) is not None and not self.match_df.empty:
            for sym, value in self.match_df.groupby('symbol')['net_pnl'].sum().items():
                pnl_by_symbol[str(sym).lower()] += float(value)

        for sym, value in self._get_open_mtm_pnl_by_symbol().items():
            pnl_by_symbol[str(sym).lower()] += float(value)

        return dict(pnl_by_symbol)

    def _calc_single_metrics(self, df_match, sym_name, strat_name, is_total=False):
        """\u6838\u5fc3\u5f15\u64ce\uff1a\u7cbe\u51c6\u8ba1\u7b97\u6bcf\u4e00\u4e2a\u7ef4\u5ea6\uff0c\u65e0\u6b7b\u89d2\u3002"""
        total_trades = len(df_match) if not df_match.empty else 0
        total_net_pnl = df_match['net_pnl'].sum() if total_trades > 0 else 0.0
        active_trade_days = 0
        actual_commission_events = self._get_commission_events(None if is_total else sym_name)
        total_commission = actual_commission_events['commission'].sum() if not actual_commission_events.empty else 0.0
        turnover_events = self._get_turnover_events(None if is_total else sym_name)
        if not turnover_events.empty:
            turnover_events['session_date'] = _trading_session_dates(
                turnover_events['datetime'], self.freq
            )
            daily_turnover = turnover_events.groupby('session_date')['turnover'].sum()
        else:
            daily_turnover = pd.Series(dtype=float)

        meta = _lookup_meta(sym_name)
        multiplier = float(meta.get('multiplier', _lookup_multiplier(sym_name)))
        tick_size = float(meta.get('tick_size', 1.0))
        margin_rate = meta.get('margin_rate')
        margin_rate_text = '-' if margin_rate is None else f"{float(margin_rate) * 100:.1f}%"

        if total_trades > 0:
            win_trades = len(df_match[df_match['net_pnl'] > 0])
            win_rate_trade = win_trades / total_trades
            avg_win = df_match[df_match['net_pnl'] > 0]['net_pnl'].mean() if win_trades > 0 else 0.0
            loss_df = df_match[df_match['net_pnl'] <= 0]['net_pnl']
            avg_loss = abs(loss_df.mean()) if len(loss_df) > 0 else 0.0
            pnl_ratio_trade = avg_win / avg_loss if avg_loss > 0 else float('inf')
            peak_t = df_match['net_pnl'].cumsum().cummax()
            max_drawdown_trade = float((df_match['net_pnl'].cumsum() - peak_t).min())

            df_match_copy = df_match.copy()
            df_match_copy['close_time'] = pd.to_datetime(df_match_copy['close_time'])
            close_session_dates = _trading_session_dates(df_match_copy['close_time'], self.freq)
            daily_pnl = df_match_copy.groupby(close_session_dates)['net_pnl'].sum()
            active_trade_days = int(close_session_dates.nunique())
        else:
            win_rate_trade = pnl_ratio_trade = max_drawdown_trade = 0.0
            daily_pnl = pd.Series(dtype=float)

        if is_total and getattr(self, 'equity_df', None) is not None and not self.equity_df.empty:
            eq = self.equity_df.copy()
            eq['datetime'] = pd.to_datetime(eq['datetime'])
            eq = eq.sort_values('datetime').drop_duplicates('datetime', keep='last')
            equity_curve = eq.set_index('datetime')['equity']
            daily_equity = (
                eq.assign(session_date=_trading_session_dates(eq['datetime'], self.freq))
                .groupby('session_date', sort=True)['equity']
                .last()
                .dropna()
            )
            sample_days = int(len(daily_equity))
            # 使用绝�?每日盈亏除以初�?资金，避免动态权�?pct_change 放大资金出入后的收益率�??
            daily_pnl_abs = daily_equity.diff().dropna()
            daily_pnl = daily_pnl_abs
            daily_returns = daily_pnl_abs / self.initial_capital

            final_equity = float(daily_equity.iloc[-1])
            max_open_value = float(eq['position_notional'].max()) if 'position_notional' in eq.columns else 0.0
        else:
            equity_curve = self.initial_capital + daily_pnl.cumsum()
            sample_days = int(len(daily_pnl))
            if equity_curve.empty:
                daily_returns = pd.Series(dtype=float)
                final_equity = self.initial_capital
            else:
                daily_returns = daily_pnl / self.initial_capital
                final_equity = float(equity_curve.iloc[-1])
            max_open_value = 0.0

        realized_cum_net = total_net_pnl
        realized_total_return = realized_cum_net / self.initial_capital if self.initial_capital > 0 else 0.0

        if is_total:
            mtm_cum_net = final_equity - self.initial_capital
        else:
            open_mtm = self._get_open_mtm_pnl_by_symbol().get(sym_name.lower(), 0.0)
            mtm_cum_net = realized_cum_net + open_mtm
        mtm_final_equity = self.initial_capital + mtm_cum_net
        mtm_total_return = mtm_cum_net / self.initial_capital if self.initial_capital > 0 else 0.0

        if not equity_curve.empty:
            peak = equity_curve.cummax()
            drawdown = (equity_curve - peak) / peak
            max_drawdown_rate = float(drawdown.min())

            if 'datetime' in self.price_df.columns:
                min_dt = pd.to_datetime(self.price_df['datetime'].min())
                max_dt = pd.to_datetime(self.price_df['datetime'].max())
            else:
                min_dt = pd.to_datetime(self.price_df.index.min())
                max_dt = pd.to_datetime(self.price_df.index.max())

            days = (max_dt - min_dt).days
            days = max(days, 1)
        else:
            max_drawdown_rate = 0.0
            days = 1

        realized_annual_return = _annualize_return(
            self.initial_capital + realized_cum_net,
            self.initial_capital,
            days
        )
        mtm_annual_return = _annualize_return(mtm_final_equity, self.initial_capital, days)
        sharpe_ratio = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if len(daily_returns) > 1 and daily_returns.std() > 0 else 0.0
        calmar_ratio = mtm_annual_return / abs(max_drawdown_rate) if max_drawdown_rate < 0 else float('inf')

        win_days = int((daily_pnl > 0).sum())
        daily_metric_days = len(daily_pnl)
        daily_win_rate = win_days / daily_metric_days if daily_metric_days > 0 else 0.0
        daily_pnl_pos = daily_pnl[daily_pnl > 0]
        daily_pnl_neg = daily_pnl[daily_pnl <= 0]
        daily_pnl_ratio = (daily_pnl_pos.mean() / abs(daily_pnl_neg.mean())) if len(daily_pnl_pos) > 0 and len(daily_pnl_neg) > 0 and daily_pnl_neg.mean() != 0 else 0.0
        avg_daily_turnover = daily_turnover.sum() / sample_days if sample_days > 0 else 0.0

        # 换月�?��统维护仓位，不�?入普通交易�?数，但手�?���?要单�?��露�??
        single_rollover_cnt = 0
        single_rollover_fee = 0.0
        if not is_total:
            for t in self.trades:
                if getattr(t, 'is_rollover', False) and t.symbol == sym_name:
                    single_rollover_fee += t.commission
                    if t.offset == Offset.OPEN:
                        single_rollover_cnt += 1
        else:
            single_rollover_cnt = self.account_summary.get('rollover_count', 0)
            single_rollover_fee = self.account_summary.get('rollover_commission', 0.0)

        rollover_cnt_text = single_rollover_cnt if single_rollover_cnt > 0 else "-"
        rollover_fee_text = f"¥{single_rollover_fee:,.0f}" if single_rollover_fee > 0 else "-"

        tick_profit = "-"
        if not is_total and tick_size > 0 and multiplier > 0 and total_trades > 0:
            tick_profit = f"{(total_net_pnl / total_trades) / (tick_size * multiplier):.1f}"

        fee_type = meta.get('fee_type', '')
        fee_open = meta.get('fee_open', 0)
        if fee_type == 'ratio':
            fee_rule = f"\u6bd4\u4f8b {float(fee_open)*10000:.1f}\u2031"
        elif fee_type == 'fixed':
            fee_rule = f"\u56fa\u5b9a {float(fee_open)}\u5143"
        else:
            fee_rule = "-"

        return {
            "\u5408\u7ea6": "\u603b\u8ba1 (MULTI)" if is_total else sym_name.upper(),
            "\u603b\u6536\u76ca": f"{realized_total_return * 100:.2f}%",
            "\u5e74\u5316\u6536\u76ca": f"{realized_annual_return * 100:.2f}%",
            "\u603b\u6536\u76ca(\u542b\u6301\u4ed3)": f"{mtm_total_return * 100:.2f}%",
            "\u5e74\u5316\u6536\u76ca(\u542b\u6301\u4ed3)": f"{mtm_annual_return * 100:.2f}%",
            "\u7d2f\u8ba1\u76c8\u4e8f": f"\u00a5{realized_cum_net:,.0f}",
            "\u5747\u7b14\u5229\u6da6(\u8df3)": tick_profit,
            "\u6700\u5927\u5f00\u4ed3\u5e02\u503c": f"\u00a5{max_open_value:,.0f}" if is_total else "-",
            "\u5355\u65e5\u6700\u5927\u56de\u64a4": f"\u00a5{max_drawdown_trade:,.0f}",
            "\u6700\u5927\u56de\u64a4\u7387": f"{max_drawdown_rate * 100:.2f}%",
            "\u5e74\u5316Sharpe": f"{sharpe_ratio:.2f}",
            "\u5361\u739b\u6bd4": f"{calmar_ratio:.2f}",
            "\u9010\u7b14\u80dc\u7387": f"{win_rate_trade * 100:.2f}%",
            "\u9010\u7b14\u76c8\u4e8f\u6bd4": f"{pnl_ratio_trade:.2f}",
            "\u9010\u65e5\u80dc\u7387": f"{daily_win_rate * 100:.2f}%",
            "\u9010\u65e5\u76c8\u4e8f\u6bd4": f"{daily_pnl_ratio:.2f}",
            "\u4ea4\u6613\u6b21\u6570": total_trades,
            "\u6210\u4ea4\u65e5\u6570": active_trade_days,
            "\u884c\u60c5\u65e5\u6570": sample_days,
            "\u65e5\u5747\u6210\u4ea4\u989d": f"\u00a5{avg_daily_turnover:,.0f}",
            "\u4fdd\u8bc1\u91d1": margin_rate_text,
            "\u4e3b\u529b\u6362\u6708\u6b21\u6570": rollover_cnt_text,
            "\u6362\u6708\u624b\u7eed\u8d39": rollover_fee_text,
            "\u8d39\u7387\u6a21\u578b": "-" if is_total else fee_rule,
            "\u7d2f\u8ba1\u624b\u7eed\u8d39": f"\u00a5{total_commission:,.0f}",
        }

    # =========================================================================
    # 🌟 以下为全新架构：后�? HTML Div 生成�?(不再调用 fig.show() �?.png)
    # =========================================================================

    def _should_use_daily_report_resolution(self, values=None) -> bool:
        """Use daily chart resolution for long intraday reports.

        This affects only embedded Plotly payloads. Raw equity, trades, exported
        CSV files, and performance metrics remain at the original backtest
        frequency.
        """

        freq = str(getattr(self, "freq", "")).lower()
        if freq not in INTRADAY_REPORT_FREQS:
            return False

        if values is None:
            if getattr(self, "equity_df", None) is not None and not self.equity_df.empty:
                values = self.equity_df["datetime"]
            elif getattr(self, "price_df", None) is not None and not self.price_df.empty:
                values = self.price_df["datetime"] if "datetime" in self.price_df.columns else self.price_df.index
            else:
                return False

        dt = pd.to_datetime(pd.Series(values), errors="coerce").dropna()
        if dt.empty:
            return False
        span_days = int((dt.max().normalize() - dt.min().normalize()).days) + 1
        return (
            span_days >= REPORT_DAILY_RESOLUTION_MIN_DAYS
            or len(dt) >= REPORT_DAILY_RESOLUTION_MIN_POINTS
        )

    def _daily_last_frame_for_report(self, df: pd.DataFrame, datetime_col: str = "datetime") -> pd.DataFrame:
        if df is None or df.empty or datetime_col not in df.columns:
            return df.copy() if df is not None else pd.DataFrame()

        out = df.copy()
        out[datetime_col] = pd.to_datetime(out[datetime_col], errors="coerce")
        out = out.dropna(subset=[datetime_col]).sort_values(datetime_col)
        if out.empty or not self._should_use_daily_report_resolution(out[datetime_col]):
            return out

        out["_report_session_date"] = _trading_session_dates(out[datetime_col], self.freq)
        out = out.groupby("_report_session_date", sort=True).tail(1).copy()
        out[datetime_col] = out["_report_session_date"]
        return out.drop(columns=["_report_session_date"])

    def _daily_last_xy_for_report(self, x_values, y_values, value_col: str):
        if x_values is None or len(x_values) == 0:
            return [], []
        df = pd.DataFrame({"datetime": list(x_values), value_col: list(y_values)})
        df = self._daily_last_frame_for_report(df, "datetime")
        return df["datetime"].tolist(), df[value_col].tolist()

    def _get_equity_series(self):
        """\u83b7\u53d6\u7528\u4e8e\u753b\u56fe\u7684\u8d44\u91d1\u6743\u76ca\u5e8f\u5217\u3002"""
        # 优先使用引擎记录的真实物理权�?
        if getattr(self, 'equity_df', None) is not None and not self.equity_df.empty:
            df = self.equity_df.sort_values('datetime').drop_duplicates('datetime', keep='last')
            df = self._daily_last_frame_for_report(df, "datetime")
            return df['datetime'].tolist(), df['equity'].tolist()

        # 兜底：�?果没记录，就用平仓流水伪造一�?
        if self.match_df.empty:
            return [], []
        df_sort = self.match_df.copy()
        df_sort['close_time'] = pd.to_datetime(df_sort['close_time'])
        daily_pnl = df_sort.sort_values('close_time').groupby(df_sort['close_time'].dt.date)['net_pnl'].sum()
        equity_curve = self.initial_capital + daily_pnl.cumsum()
        return list(equity_curve.index), equity_curve.tolist()

    def get_equity_html_div(self):
        """\u751f\u6210\u52a8\u6001\u6743\u76ca\u66f2\u7ebf\u7684 HTML div\u3002"""
        if self.match_df.empty and (self.equity_df is None or self.equity_df.empty):
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u8d44\u91d1\u6743\u76ca\u6570\u636e</div>"

        equity_x, equity_y = self._get_equity_series()
        time_axis = _build_continuous_time_axis(equity_x)
        plot_x, time_labels = _map_to_continuous_time_axis(equity_x, time_axis)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=equity_x, y=equity_y, mode='lines', name='\u52a8\u6001\u6743\u76ca',
            line=dict(color='#3b82f6', width=2), fill='tozeroy', fillcolor='rgba(59,130,246,0.1)'
        ))
        fig.data[-1].x = plot_x
        fig.data[-1].customdata = time_labels
        fig.data[-1].hovertemplate = "Time: %{customdata}<br>Equity: ¥%{y:,.0f}<extra></extra>"
        fig.update_layout(
            height=400, margin=REPORT_OVERVIEW_MARGIN,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified", xaxis_title="", yaxis_title="\u6743\u76ca (\u00a5)"
        )
        # �?��回图�?div，整�?HTML �?frontend_index.py 统一组�?�?
        fig.update_layout(hovermode="x" if time_axis.get("enabled") else "x unified")
        fig.update_xaxes(**time_axis["layout"])
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_cum_pnl_html_div(self):
        """\u751f\u6210\u7d2f\u8ba1\u76c8\u4e8f\u4e0e\u624b\u7eed\u8d39\u7684\u53cc Y \u8f74 HTML div\u3002"""
        equity_x, equity_y = self._get_equity_series()
        if not equity_y:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u8d44\u91d1\u6570\u636e</div>"

        # �??盈亏 = 动�?�权�?- 初�?�?��
        cum_pnl = [val - self.initial_capital for val in equity_y]

        # �??交易手续费使用真实成交流水，包含�?��仓开仓手�?��和换月手�?���?
        commission_events = self._get_commission_events()
        if not commission_events.empty:
            commission_events = commission_events.sort_values('datetime')
            comm_x = commission_events['datetime'].tolist()
            comm_y = commission_events['commission'].cumsum().tolist()
            comm_x, comm_y = self._daily_last_xy_for_report(comm_x, comm_y, "commission")
        else:
            comm_x = equity_x
            comm_y = [0] * len(equity_x)

        time_axis = _build_continuous_time_axis(equity_x)
        plot_x, time_labels = _map_to_continuous_time_axis(equity_x, time_axis)
        comm_plot_x, comm_time_labels = _map_to_continuous_time_axis(comm_x, time_axis)

        fig = go.Figure()

        # �??盈亏曲线�?
        fig.add_trace(go.Scatter(
            x=equity_x, y=cum_pnl,
            mode='lines', name='\u7d2f\u8ba1\u76c8\u4e8f', line=dict(color='#ef4444', width=2),
            fill='tozeroy', fillcolor='rgba(239,68,68,0.1)',
            yaxis='y1',
            hovertemplate="\u65e5\u671f: %{x}<br>\u7d2f\u8ba1\u76c8\u4e8f: \u00a5%{y:,.0f}<extra></extra>"
        ))

        # �??手续费曲线�??
        fig.add_trace(go.Scatter(
            x=comm_x, y=comm_y,
            mode='lines', name='\u7d2f\u8ba1\u4ea4\u6613\u624b\u7eed\u8d39', line=dict(color='#22c55e', width=2),
            hovertemplate="\u65e5\u671f: %{x}<br>\u7d2f\u8ba1\u624b\u7eed\u8d39: \u00a5%{y:,.0f}<extra></extra>"
        ))

        fig.data[0].x = plot_x
        fig.data[0].customdata = time_labels
        fig.data[0].hovertemplate = "Time: %{customdata}<br>Cumulative PnL: ¥%{y:,.0f}<extra></extra>"
        fig.data[1].x = comm_plot_x
        fig.data[1].customdata = comm_time_labels
        fig.data[1].hovertemplate = "Time: %{customdata}<br>Cumulative Fee: ¥%{y:,.0f}<extra></extra>"

        fig.update_layout(
            height=350,
            margin=REPORT_OVERVIEW_MARGIN,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),

            yaxis=dict(
                title=dict(text="\u7d2f\u8ba1\u76c8\u4e8f (\u00a5)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                showgrid=True,
                gridcolor='#f3f4f6'
            ),

            yaxis2=dict(
                title=dict(text="\u7d2f\u8ba1\u624b\u7eed\u8d39 (\u00a5)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                anchor="x",
                overlaying="y",
                side="right",
                showgrid=False
            )
        )

        fig.update_layout(yaxis2=dict(visible=False, showticklabels=False, title=None))
        fig.update_layout(hovermode="x" if time_axis.get("enabled") else "x unified")
        fig.update_xaxes(**time_axis["layout"])
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_net_value_with_benchmark_html_div(self):
        """\u751f\u6210\u51c0\u503c\u66f2\u7ebf\u4e0e\u57fa\u51c6\u5bf9\u6bd4 HTML div\u3002"""
        equity_x, equity_y = self._get_equity_series()
        if not equity_y:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u8d44\u91d1\u6570\u636e</div>"

        # 1. 计算策略绝�?�?�?
        strategy_nv = [val / self.initial_capital for val in equity_y]
        time_axis = _build_continuous_time_axis(equity_x)
        plot_x, time_labels = _map_to_continuous_time_axis(equity_x, time_axis)

        # 2. 提取底层价格，�?算等权基准净�?(Buy & Hold)
        df_price = self.price_df.copy()
        if isinstance(df_price.columns, pd.MultiIndex):
            if 'close' in df_price.columns.levels[0]:
                close_df = df_price['close']
            elif 'last_price' in df_price.columns.levels[0]:
                close_df = df_price['last_price']
            else:
                close_df = pd.DataFrame()
        else:
            close_df = df_price

        # 对齐时间轴与计算
        if not close_df.empty:
            # 每个品�?从自己的�?��条有效价格归�?化，避免�?��市品种的 NaN �?��基准�?
            norm_df = close_df.apply(lambda col: col / col.dropna().iloc[0] if not col.dropna().empty else col)
            # 等权均�?�会�?��忽略 NaN，用于模拟动态等权买入持有基准�??
            benchmark_nv = norm_df.mean(axis=1)

            # 对齐到动态权益的时间�?
            bench_series = pd.Series(benchmark_nv.values, index=pd.to_datetime(close_df.index))
            bench_series = bench_series[~bench_series.index.duplicated(keep='last')]
            idx = pd.to_datetime(equity_x)
            aligned_bench = bench_series.reindex(idx, method='ffill').bfill()
            bench_y = aligned_bench.tolist()
        else:
            bench_y = [1.0] * len(equity_x)

        fig = go.Figure()

        # 策略�?值�??
        fig.add_trace(go.Scatter(
            x=equity_x, y=strategy_nv,
            mode='lines', name='\u7b56\u7565\u51c0\u503c', line=dict(color='#3b82f6', width=2),
            fill='tozeroy', fillcolor='rgba(59,130,246,0.1)',
            hovertemplate="\u65e5\u671f: %{x}<br>\u7b56\u7565\u51c0\u503c: %{y:.4f}<extra></extra>"
        ))

        # 等权买入持有基准�?值�??
        fig.add_trace(go.Scatter(
            x=equity_x, y=bench_y,
            mode='lines', name='等权买入持有基准', line=dict(color='#f59e0b', width=2, dash='dash'),
            hovertemplate="\u65e5\u671f: %{x}<br>\u57fa\u51c6\u51c0\u503c: %{y:.4f}<extra></extra>"
        ))

        fig.data[0].x = plot_x
        fig.data[0].customdata = time_labels
        fig.data[0].hovertemplate = "Time: %{customdata}<br>Strategy NAV: %{y:.4f}<extra></extra>"
        fig.data[1].x = plot_x
        fig.data[1].customdata = time_labels
        fig.data[1].hovertemplate = "Time: %{customdata}<br>Benchmark NAV: %{y:.4f}<extra></extra>"

        fig.update_layout(
            height=350,
            margin=REPORT_OVERVIEW_MARGIN,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(
                title=dict(text="\u51c0\u503c", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                showgrid=True,
                gridcolor='#f3f4f6'
            )
        )

        fig.update_layout(hovermode="x" if time_axis.get("enabled") else "x unified")
        fig.update_xaxes(**time_axis["layout"])
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_rolling_drawdown_html_div(self):
        """\u751f\u6210\u6eda\u52a8\u56de\u64a4 (Rolling Drawdown) \u65f6\u5e8f\u6298\u7ebf\u56fe\u3002"""
        if getattr(self, 'equity_df', None) is None or self.equity_df.empty:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u8d44\u91d1\u6570\u636e\uff0c\u65e0\u6cd5\u8ba1\u7b97\u6eda\u52a8\u56de\u64a4</div>"

        df_eq = self.equity_df.sort_values('datetime').copy()

        # 1. 计算滚动�?高点及回撤比�?
        peak = df_eq['equity'].cummax()
        # �?���???list，避�?Plotly �?pandas 索引�??为坐标�??
        drawdown_df = pd.DataFrame({
            "datetime": pd.to_datetime(df_eq["datetime"], errors="coerce"),
            "drawdown": ((df_eq['equity'] - peak) / peak),
        }).dropna(subset=["datetime"])
        if self._should_use_daily_report_resolution(drawdown_df["datetime"]):
            drawdown_df["_report_session_date"] = _trading_session_dates(drawdown_df["datetime"], self.freq)
            drawdown_df = (
                drawdown_df
                .groupby("_report_session_date", sort=True, as_index=False)
                .agg(drawdown=("drawdown", "min"))
                .rename(columns={"_report_session_date": "datetime"})
            )
        drawdown_y = drawdown_df["drawdown"].tolist()
        drawdown_x = drawdown_df['datetime'].tolist()
        time_axis = _build_continuous_time_axis(drawdown_x)
        plot_x, time_labels = _map_to_continuous_time_axis(drawdown_x, time_axis)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=drawdown_x, y=drawdown_y,
            mode='lines', name='\u52a8\u6001\u56de\u64a4',
            line=dict(color='#dc2626', width=1.5),
            fill='tozeroy', fillcolor='rgba(220,38,38,0.08)',
            hovertemplate="日期: %{x}<br>回撤幅度: %{y:.2%}<extra></extra>"
        ))

        fig.data[-1].x = plot_x
        fig.data[-1].customdata = time_labels
        fig.data[-1].hovertemplate = "Time: %{customdata}<br>Drawdown: %{y:.2%}<extra></extra>"

        fig.update_layout(
            height=300, margin=REPORT_OVERVIEW_MARGIN,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            yaxis=dict(
                title=dict(text="回撤幅度", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                tickformat=".2%",  # 恢�?百分比格�?
                showgrid=True,
                gridcolor='#f3f4f6'
            )
        )
        fig.update_layout(hovermode="x" if time_axis.get("enabled") else "x unified")
        fig.update_xaxes(**time_axis["layout"])
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_leverage_and_position_html_div(self):
        """\u8fd8\u539f\u6bcf\u65e5\u5386\u53f2\u4ed3\u4f4d\u72b6\u6001\uff0c\u751f\u6210\u591a\u7a7a\u6301\u4ed3\u540d\u4e49\u672c\u91d1\u4e0e\u9694\u591c\u6760\u6746\u7387\u53cc\u8f74\u56fe\u3002"""
        if not self.trades or getattr(self, 'equity_df', None) is None or self.equity_df.empty:
            return "<div class=\'text-center text-gray-500 py-10\'>\u6301\u4ed3\u6216\u6743\u76ca\u6570\u636e\u4e0d\u8db3\uff0c\u65e0\u6cd5\u8ba1\u7b97\u6760\u6746\u7387</div>"

        df_eq = self.equity_df.sort_values('datetime').copy()
        if 'position_notional' in df_eq.columns:
            df_eq['position_notional'] = pd.to_numeric(df_eq['position_notional'], errors='coerce').fillna(0.0)
            df_eq['equity'] = pd.to_numeric(df_eq['equity'], errors='coerce').replace(0, np.nan)
            has_directional_exposure = {
                'long_position_notional',
                'short_position_notional',
            }.issubset(df_eq.columns)
            if has_directional_exposure:
                df_eq['long_position_notional'] = pd.to_numeric(
                    df_eq['long_position_notional'], errors='coerce'
                ).fillna(0.0)
                df_eq['short_position_notional'] = pd.to_numeric(
                    df_eq['short_position_notional'], errors='coerce'
                ).fillna(0.0)
                df_eq['position_notional'] = (
                    df_eq['long_position_notional'].abs()
                    + df_eq['short_position_notional'].abs()
                )
            df_eq['leverage'] = (df_eq['position_notional'].abs() / df_eq['equity']).fillna(0.0)
            if self._should_use_daily_report_resolution(df_eq["datetime"]):
                df_eq["_report_session_date"] = _trading_session_dates(df_eq["datetime"], self.freq)
                agg_map = {
                    "datetime": "last",
                    "equity": "last",
                    "position_notional": lambda s: float(pd.to_numeric(s, errors="coerce").abs().max()),
                    "leverage": "max",
                }
                if has_directional_exposure:
                    agg_map["long_position_notional"] = lambda s: float(pd.to_numeric(s, errors="coerce").abs().max())
                    agg_map["short_position_notional"] = lambda s: float(pd.to_numeric(s, errors="coerce").abs().max())
                df_eq = df_eq.groupby("_report_session_date", sort=True).agg(agg_map).reset_index()
                df_eq["datetime"] = df_eq["_report_session_date"]

            time_axis = _build_continuous_time_axis(df_eq['datetime'])
            plot_x, time_labels = _map_to_continuous_time_axis(df_eq['datetime'], time_axis)

            fig = go.Figure()
            if has_directional_exposure:
                fig.add_trace(go.Bar(
                    x=plot_x,
                    y=df_eq['long_position_notional'].abs(),
                    name='多头持仓名义本金',
                    marker_color='rgba(239, 68, 68, 0.60)',
                    yaxis='y1',
                    customdata=time_labels,
                    hovertemplate="时间: %{customdata}<br>多头持仓名义本金: ￥%{y:,.0f}<extra></extra>",
                ))
                fig.add_trace(go.Bar(
                    x=plot_x,
                    y=-df_eq['short_position_notional'].abs(),
                    name='空头持仓名义本金',
                    marker_color='rgba(34, 197, 94, 0.60)',
                    yaxis='y1',
                    customdata=np.column_stack([time_labels, df_eq['short_position_notional'].abs()]),
                    hovertemplate="时间: %{customdata[0]}<br>空头持仓名义本金: ￥%{customdata[1]:,.0f}<extra></extra>",
                ))
            else:
                fig.add_trace(go.Bar(
                    x=plot_x,
                    y=df_eq['position_notional'].abs(),
                    name='总持仓名义本金',
                    marker_color='rgba(37, 99, 235, 0.55)',
                    yaxis='y1',
                    customdata=time_labels,
                    hovertemplate="时间: %{customdata}<br>持仓名义本金: ￥%{y:,.0f}<extra></extra>",
                ))
            fig.add_trace(go.Scatter(
                x=plot_x,
                y=df_eq['leverage'],
                mode='lines',
                name='实时总杠杆率',
                line=dict(color='#111827', width=2),
                yaxis='y2',
                customdata=time_labels,
                hovertemplate="时间: %{customdata}<br>实时总杠杆率: %{y:.2f} 倍<extra></extra>",
            ))
            fig.update_layout(
                height=380,
                margin=REPORT_OVERVIEW_MARGIN,
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                hovermode="x unified",
                barmode='relative',
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                yaxis=dict(
                    title=dict(text="名义持仓本金 (￥)", font=dict(color="#1f2937")),
                    tickfont=dict(color="#1f2937"),
                    gridcolor='#f3f4f6',
                ),
                yaxis2=dict(
                    title=dict(text="实时总杠杆率 (倍)", font=dict(color="#1f2937")),
                    tickfont=dict(color="#1f2937"),
                    anchor="x",
                    overlaying="y",
                    side="right",
                    showgrid=False,
                    tickformat=".2f",
                    rangemode="tozero",
                ),
            )
            fig.update_xaxes(**time_axis["layout"])
            return fig.to_html(full_html=False, include_plotlyjs=False)

        # 1. 建立每日持仓时间�?
        from collections import defaultdict
        df_eq = self.equity_df.sort_values('datetime').copy()
        df_eq['date'] = pd.to_datetime(df_eq['datetime']).dt.date
        date_list = sorted(df_eq['date'].unique())

        # 整理�?有交易流�?
        trade_records = []
        for t in self.trades:
            trade_records.append({
                'time': pd.to_datetime(t.trade_time),
                'date': pd.to_datetime(t.trade_time).date(),
                'symbol': t.symbol.lower(),
                'direction': t.direction,
                'offset': t.offset,
                'volume': t.volume,
                'price': t.price
            })
        df_trades = pd.DataFrame(trade_records).sort_values('time') if trade_records else pd.DataFrame()

        # 2. 状�?�机动�?�模拟每日持仓量
        daily_positions = {}
        current_pos = defaultdict(int)  # 符号代表方向：�?为�?，负为空

        # 建立价格字典加�?�查�?
        price_records = []
        # 解析 price_df 宽表�?��收盘�?
        for dt, row in self.price_df.iterrows():
            d = dt.date()
            p_dict = {}
            for col in self.price_df.columns:
                if isinstance(col, tuple):
                    if col[0] in ['close', 'last_price']:
                        for key in _symbol_price_lookup_keys(col[1]):
                            p_dict[key] = row[col]
                else:
                    for key in _symbol_price_lookup_keys(col):
                        p_dict[key] = row[col]
            price_records.append({'date': d, 'prices': p_dict})
        daily_prices = {r['date']: r['prices'] for r in price_records}

        daily_long_val = []
        daily_short_val = []
        daily_leverage = []

        # 线�?��?进回�?���?�?
        for d in date_list:
            if not df_trades.empty:
                day_trades = df_trades[df_trades['date'] == d]
                for _, t in day_trades.iterrows():
                    sym = t['symbol']
                    vol = t['volume']
                    if t['offset'] in [Offset.OPEN]:
                        if t['direction'] == Direction.LONG:
                            current_pos[sym] += vol
                        else:
                            current_pos[sym] -= vol
                    else:
                        if t['direction'] == Direction.LONG:
                            current_pos[sym] += vol  # 平空仓位增加
                        else:
                            current_pos[sym] -= vol  # 平�?仓位减少

            # 计算当天的名义市�?
            p_map = daily_prices.get(d, {})
            long_v = 0.0
            short_v = 0.0

            for sym, vol in current_pos.items():
                if vol == 0: continue
                mult = _lookup_multiplier(sym)
                c_price = 0.0
                for key in _symbol_price_lookup_keys(sym):
                    c_price = p_map.get(key, 0.0)
                    if c_price != 0.0:
                        break
                if c_price == 0.0: continue

                if vol > 0:
                    long_v += vol * c_price * mult
                else:
                    short_v += abs(vol) * c_price * mult

            daily_long_val.append(long_v)
            daily_short_val.append(-short_v)  # 空头名义�?���?��负数用于条形图向下堆�?

            # 查找当天的权�?
            day_equity = df_eq[df_eq['date'] == d]['equity'].iloc[-1]
            total_nominal = long_v + short_v
            daily_leverage.append(total_nominal / day_equity if day_equity > 0 else 0.0)

        # 3. �?�?Plotly 机构级双轴渲�?
        fig = go.Figure()

        # 多头总敞�?(上方条形�?
        fig.add_trace(go.Bar(
            x=date_list, y=daily_long_val,
            name='\u591a\u5934\u603b\u655e\u53e3', marker_color='rgba(239, 68, 68, 0.6)', yaxis='y1',
            hovertemplate="\u65e5\u671f: %{x}<br>\u591a\u5934\u6301\u4ed3\u540d\u4e49\u4ef7\u503c: \u00a5%{y:,.0f}<extra></extra>"
        ))

        # 空头总敞�?(下方条形�?
        fig.add_trace(go.Bar(
            x=date_list, y=daily_short_val,
            name='\u7a7a\u5934\u603b\u655e\u53e3', marker_color='rgba(34, 197, 94, 0.6)', yaxis='y1',
            hovertemplate="\u65e5\u671f: %{x}<br>\u7a7a\u5934\u6301\u4ed3\u540d\u4e49\u4ef7\u503c: \u00a5%{y:,.0f}<extra></extra>"
        ))

        # 隔�?总杠杆率 (覆盖折线) - 绑定到副�?y2
        fig.add_trace(go.Scatter(
            x=date_list, y=daily_leverage,
            mode='lines', name='\u9694\u591c\u603b\u6760\u6746\u7387', line=dict(color='#111827', width=2), yaxis='y2',
            hovertemplate="\u65e5\u671f: %{x}<br>\u5b9e\u9645\u9694\u591c\u6760\u6746\u7387: %{y:.2f} \u500d<extra></extra>"
        ))

        fig.update_layout(
            height=380, margin=REPORT_OVERVIEW_MARGIN,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified", barmode='relative',
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),

            # 主轴：�?�敞口名义本�?
            yaxis=dict(
                title=dict(text="\u540d\u4e49\u6301\u4ed3\u672c\u91d1 (\u00a5)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937")),

            # �?��：实际杠杆率
            yaxis2=dict(
                title=dict(text="\u5b9e\u9645\u9694\u591c\u6760\u6746\u7387 (\u500d)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                anchor="x", overlaying="y", side="right", showgrid=False,
                tickformat=".2f"
            )
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_multi_asset_pnl_bar_html_div(self):
        """生成多品种按盈亏从高到低排序的垂直直方图 (#11)"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty or self.symbol != 'MULTI':
            return "<div class=\'text-center text-gray-500 py-10\'>\u975e\u591a\u54c1\u79cd\u7ec4\u5408\u6216\u65e0\u4ea4\u6613\u6570\u636e\uff0c\u4e0d\u751f\u6210\u54c1\u79cd\u76c8\u4e8f\u76f4\u65b9\u56fe</div>"

        # 1. 按品种聚合盈亏并严格降序排列
        asset_stats = self.match_df.groupby('symbol')['net_pnl'].sum().sort_values(ascending=False)

        # �?���???Python 列表，避�?Plotly �?? pandas 索引�?
        symbols = [str(sym).upper() for sym in asset_stats.index]
        pnls = [float(val) for val in asset_stats.values]

        # 盈利为红，亏损为�?(国内期货标准)
        colors = ['#dc2626' if val > 0 else '#16a34a' for val in pnls]

        def format_pnl_label(value: float) -> str:
            abs_value = abs(value)
            if abs_value >= 100000000:
                return f"{value / 100000000:.1f}\u4ebf"
            if abs_value >= 10000:
                return f"{value / 10000:.0f}\u4e07"
            return f"{value:,.0f}"

        fig = go.Figure()

        # 显式指定 x �?y，确保输出为标准垂直柱状图�??
        fig.add_trace(go.Bar(
            x=symbols,
            y=pnls,
            marker_color=colors,
            text=[format_pnl_label(val) for val in pnls],
            textposition='auto',
            hovertemplate="\u5408\u7ea6: %{x}<br>\u7d2f\u8ba1\u51c0\u76c8\u4e8f: \u00a5%{y:,.0f}<extra></extra>"
        ))

        fig.update_layout(
            height=350, margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            showlegend=False,
            yaxis=dict(
                title=dict(text="\u7d2f\u8ba1\u51c0\u76c8\u4e8f (\u00a5)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                showgrid=True, gridcolor='#f3f4f6'
            ),
            xaxis=dict(tickfont=dict(color="#1f2937", size=12, weight="bold"))
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_holding_period_pie_html_div(self):
        """\u751f\u6210\u4ea4\u6613\u7684\u6301\u4ed3\u5468\u671f\u5360\u6bd4\u997c\u56fe\u3002"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u4ea4\u6613\u660e\u7ec6</div>"

        df_match = self.match_df.copy()

        df_match['close_time'] = pd.to_datetime(df_match['close_time'])
        df_match['open_time'] = pd.to_datetime(df_match['open_time'])

        if self.freq in {'1d', 'd', 'day', 'daily'}:
            hold_days = (
                df_match['close_time'].dt.normalize() - df_match['open_time'].dt.normalize()
            ).dt.days

            def _categorize_days(days):
                if days <= 0:
                    return "\u540c\u65e5\u6210\u4ea4 (0\u5929)"
                if days <= 5:
                    return "\u77ed\u7ebf (1-5\u5929)"
                if days <= 15:
                    return "\u4e2d\u7ebf (6-15\u5929)"
                return "\u957f\u7ebf (>15\u5929)"

            df_match['duration_class'] = hold_days.apply(_categorize_days)
            order = ["\u540c\u65e5\u6210\u4ea4 (0\u5929)", "\u77ed\u7ebf (1-5\u5929)", "\u4e2d\u7ebf (6-15\u5929)", "\u957f\u7ebf (>15\u5929)"]
        else:
            hold_minutes = (df_match['close_time'] - df_match['open_time']).dt.total_seconds() / 60.0

            def _categorize_minutes(mins):
                if mins <= 5.0:
                    return "\u9ad8\u9891 (\u5c0f\u4e8e5\u5206\u949f)"
                if mins <= 24 * 60.0:
                    return "\u65e5\u5185 (5\u5206\u949f-1\u5929)"
                if mins <= 5 * 24 * 60.0:
                    return "\u77ed\u7ebf (1-5\u5929)"
                if mins <= 15 * 24 * 60.0:
                    return "\u4e2d\u7ebf (5-15\u5929)"
                return "\u957f\u7ebf (>15\u5929)"

            df_match['duration_class'] = hold_minutes.apply(_categorize_minutes)
            order = ["\u9ad8\u9891 (\u5c0f\u4e8e5\u5206\u949f)", "\u65e5\u5185 (5\u5206\u949f-1\u5929)", "\u77ed\u7ebf (1-5\u5929)", "\u4e2d\u7ebf (5-15\u5929)", "\u957f\u7ebf (>15\u5929)"]
        duration_counts = df_match['duration_class'].value_counts().reindex(order).dropna()

        fig = go.Figure(data=[go.Pie(
            labels=duration_counts.index.tolist(),
            values=duration_counts.values.tolist(),
            textinfo='label+percent',
            hole=0.4,
            marker=dict(colors=['#1e3a8a', '#2563eb', '#3b82f6', '#93c5fd', '#dbeafe'])
        )])

        fig.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            showlegend=False
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_turnover_pie_html_div(self):
        """Generate trade-notional allocation from actual fills; color encodes symbol PnL."""
        turnover_events = self._get_turnover_events()
        if turnover_events.empty:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u4ea4\u6613\u660e\u7ec6</div>"

        turnover_stats = turnover_events.groupby('symbol')['turnover'].sum().sort_values(ascending=False)
        pnl_by_symbol = self._get_symbol_total_pnl()

        if len(turnover_stats) >= 10:
            total_turnover = float(turnover_stats.sum())
            df_turnover = turnover_stats.rename("turnover").reset_index()
            df_turnover["sector"] = df_turnover["symbol"].apply(_symbol_sector)
            df_turnover["pct"] = df_turnover["turnover"] / total_turnover * 100 if total_turnover > 0 else 0.0
            df_turnover["net_pnl"] = df_turnover["symbol"].map(lambda sym: pnl_by_symbol.get(str(sym).lower(), 0.0))
            df_turnover = df_turnover.sort_values(
                by="symbol",
                key=lambda series: series.map(_symbol_sort_key),
            )

            sector_stats = df_turnover.groupby("sector", sort=False).agg(
                turnover=("turnover", "sum"),
                net_pnl=("net_pnl", "sum"),
            )
            max_abs_pnl = max(
                [abs(float(value)) for value in df_turnover["net_pnl"].tolist()]
                + [abs(float(value)) for value in sector_stats["net_pnl"].tolist()]
                + [1.0]
            )
            labels = []
            parents = []
            ids = []
            values = []
            colors = []
            customdata = []
            text = []

            for sector, row in sector_stats.iterrows():
                turnover = float(row["turnover"])
                net_pnl = float(row["net_pnl"])
                sector_pct = float(turnover / total_turnover * 100) if total_turnover > 0 else 0.0
                labels.append(sector)
                parents.append("")
                ids.append(f"sector:{sector}")
                values.append(turnover)
                colors.append(_pnl_to_color(net_pnl, max_abs_pnl))
                customdata.append([sector, turnover, sector_pct, net_pnl])
                text.append(f"{sector_pct:.1f}%")

            for row in df_turnover.itertuples(index=False):
                symbol_label = str(row.symbol).upper()
                labels.append(symbol_label)
                parents.append(f"sector:{row.sector}")
                ids.append(f"symbol:{symbol_label}")
                values.append(float(row.turnover))
                colors.append(_pnl_to_color(float(row.net_pnl), max_abs_pnl))
                customdata.append([row.sector, float(row.turnover), float(row.pct), float(row.net_pnl)])
                text.append(f"{row.pct:.2f}%")

            fig = go.Figure(data=[go.Treemap(
                labels=labels,
                parents=parents,
                ids=ids,
                values=values,
                branchvalues="total",
                text=text,
                textinfo="label+text",
                customdata=customdata,
                marker=dict(
                    colors=colors,
                    line=dict(width=1.5, color="#ffffff"),
                ),
                hovertemplate=(
                    "合约/板块: %{label}<br>"
                    "\u6240\u5c5e\u677f\u5757: %{customdata[0]}<br>"
                    "\u4ea4\u6613\u5e02\u503c: \u00a5%{customdata[1]:,.0f}<br>"
                    "\u5e02\u503c\u5360\u6bd4: %{customdata[2]:.2f}%<br>"
                    "\u51c0\u6536\u76ca: \u00a5%{customdata[3]:,.0f}<extra></extra>"
                ),
            )])

            fig.update_layout(
                height=340, margin=dict(l=4, r=4, t=4, b=4),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            )
            return fig.to_html(full_html=False, include_plotlyjs=False)

        total_turnover = float(turnover_stats.sum())
        df_turnover = turnover_stats.rename("turnover").reset_index()
        df_turnover["pct"] = df_turnover["turnover"] / total_turnover * 100 if total_turnover > 0 else 0.0
        df_turnover["net_pnl"] = df_turnover["symbol"].map(lambda sym: pnl_by_symbol.get(str(sym).lower(), 0.0))
        max_abs_pnl = max([abs(float(value)) for value in df_turnover["net_pnl"].tolist()] + [1.0])

        fig = go.Figure(data=[go.Pie(
            labels=[str(sym).upper() for sym in df_turnover["symbol"]],
            values=df_turnover["turnover"].tolist(),
            textinfo='label+percent',
            hole=0.4,
            customdata=df_turnover[["turnover", "pct", "net_pnl"]].values.tolist(),
            marker=dict(colors=[_pnl_to_color(float(pnl), max_abs_pnl) for pnl in df_turnover["net_pnl"]]),
            hovertemplate=(
                "合约: %{label}<br>"
                "\u4ea4\u6613\u5e02\u503c: \u00a5%{customdata[0]:,.0f}<br>"
                "\u5e02\u503c\u5360\u6bd4: %{customdata[1]:.2f}%<br>"
                "\u51c0\u6536\u76ca: \u00a5%{customdata[2]:,.0f}<extra></extra>"
            ),
        )])

        fig.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            showlegend=False
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_multi_asset_pnl_curves_html_div(self):
        """\u751f\u6210\u591a\u54c1\u79cd\u7d2f\u8ba1\u76c8\u4e8f\u66f2\u7ebf\uff0c\u9ed8\u8ba4\u5168\u90e8\u54c1\u79cd\uff0c\u5e76\u652f\u6301\u677f\u5757\u5408\u8ba1\u89c6\u56fe\u3002"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty or self.symbol != 'MULTI':
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u591a\u54c1\u79cd\u4ea4\u6613\u6d41\u6c34</div>"

        df = self.match_df.copy()
        df['close_time'] = pd.to_datetime(df['close_time'])

        fig = go.Figure()

        equity_x, _ = self._get_equity_series()
        base_idx = pd.DatetimeIndex(pd.to_datetime(equity_x)).normalize().drop_duplicates() if len(equity_x) > 0 else None

        grouped = []
        for sym, grp in df.groupby('symbol'):
            grouped.append((sym, _symbol_sector(sym), grp))
        grouped.sort(key=lambda item: _symbol_sort_key(item[0]))

        symbol_series = []
        index_union = pd.DatetimeIndex([])
        for sym, sector, grp in grouped:
            daily_pnl = grp.groupby(grp['close_time'].dt.normalize())['net_pnl'].sum()
            if base_idx is not None and not base_idx.empty:
                daily_pnl = daily_pnl.reindex(base_idx).fillna(0)
            else:
                index_union = index_union.union(daily_pnl.index)

            symbol_series.append({
                "symbol": sym,
                "sector": sector,
                "daily_pnl": daily_pnl,
            })

        if (base_idx is None or base_idx.empty) and not index_union.empty:
            index_union = index_union.sort_values()
            for item in symbol_series:
                item["daily_pnl"] = item["daily_pnl"].reindex(index_union).fillna(0)

        sectors = []
        for item in symbol_series:
            sector = item["sector"]
            if sector not in sectors:
                sectors.append(sector)

        sector_daily = {}
        for sector in sectors:
            sector_items = [item["daily_pnl"] for item in symbol_series if item["sector"] == sector]
            if not sector_items:
                continue
            sector_daily[sector] = pd.concat(sector_items, axis=1).sum(axis=1)

        trace_meta = []
        for sector in sectors:
            daily_pnl = sector_daily.get(sector)
            if daily_pnl is None:
                continue
            cum_pnl = daily_pnl.cumsum()
            fig.add_trace(go.Scatter(
                x=cum_pnl.index.strftime('%Y-%m-%d').tolist(),
                y=cum_pnl.tolist(),
                mode='lines',
                name=f'{sector} \u5408\u8ba1',
                line=dict(width=3),
                legendgroup=f"sector_total:{sector}",
                visible=False,
                customdata=[sector] * len(cum_pnl),
                hovertemplate=(
                    "板块: %{customdata}<br>"
                    "日期: %{x}<br>"
                    "\u677f\u5757\u7d2f\u8ba1\u76c8\u4e8f: \u00a5%{y:,.0f}<extra></extra>"
                )
            ))
            trace_meta.append({"sector": sector, "kind": "sector"})

        seen_symbol_sectors = set()
        for item in symbol_series:
            sym = item["symbol"]
            sector = item["sector"]
            cum_pnl = item["daily_pnl"].cumsum()
            sym_label = str(sym).upper()
            legend_title = sector if sector not in seen_symbol_sectors else None
            seen_symbol_sectors.add(sector)
            fig.add_trace(go.Scatter(
                x=cum_pnl.index.strftime('%Y-%m-%d').tolist(),
                y=cum_pnl.tolist(),
                mode='lines',
                name=sym_label,
                line=dict(width=1.4),
                legendgroup=f"sector_symbols:{sector}",
                legendgrouptitle_text=legend_title,
                visible=True,
                customdata=[sector] * len(cum_pnl),
                hovertemplate=(
                    f"合约: {sym_label}<br>"
                    "板块: %{customdata}<br>"
                    "日期: %{x}<br>"
                    "\u7d2f\u8ba1\u76c8\u4e8f: \u00a5%{y:,.0f}<extra></extra>"
                )
            ))
            trace_meta.append({"sector": sector, "kind": "symbol", "symbol": sym_label})

        view_buttons = [
            dict(
                label="\u5168\u90e8\u54c1\u79cd",
                method="update",
                args=[
                    {"visible": [item["kind"] == "symbol" for item in trace_meta]},
                    {"title": None},
                ],
            ),
            dict(
                label="\u677f\u5757\u5408\u8ba1",
                method="update",
                args=[
                    {"visible": [item["kind"] == "sector" for item in trace_meta]},
                    {"title": None},
                ],
            )
        ]

        fig.update_layout(
            height=620, margin=dict(l=10, r=10, t=86, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            updatemenus=[dict(
                type="buttons",
                direction="right",
                active=0,
                x=0,
                y=1.12,
                xanchor="left",
                yanchor="top",
                buttons=view_buttons,
                bgcolor="#ffffff",
                bordercolor="#d1d5db",
                font=dict(color="#111827", size=12),
                pad={"r": 8, "t": 4},
            )],
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                traceorder="grouped",
                groupclick="toggleitem",
                font=dict(size=10),
            ),
            yaxis=dict(
                title=dict(text="\u7d2f\u8ba1\u76c8\u4e8f (\u00a5)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"), showgrid=True, gridcolor='#f3f4f6'
            )
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_pnl_distribution_html_div(self):
        """\u751f\u6210\u9010\u7b14\u51c0\u76c8\u4e8f\u5206\u5e03\u56fe\uff0c\u7eb5\u8f74\u4e3a\u4ea4\u6613\u7b14\u6570\u5360\u6bd4\u3002"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u4ea4\u6613\u660e\u7ec6</div>"

        df = self.match_df.copy()
        pnl_values = pd.to_numeric(df['net_pnl'], errors='coerce').dropna().to_numpy(dtype=float)
        if pnl_values.size == 0:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u6709\u6548\u76c8\u4e8f\u6570\u636e</div>"

        def _build_side_bins(values: np.ndarray, side: str) -> list[dict]:
            if values.size == 0:
                return []

            side_min = float(np.min(values))
            side_max = float(np.max(values))
            bin_count = min(15, max(5, int(np.sqrt(values.size))))

            if side == "loss":
                left_edge = side_min
                right_edge = 0.0
                color = '#16a34a'
            else:
                left_edge = 0.0
                right_edge = side_max
                color = '#dc2626'

            if left_edge == right_edge:
                pad = max(abs(left_edge) * 0.1, 1.0)
                left_edge -= pad
                right_edge += pad

            edges = np.linspace(left_edge, right_edge, bin_count + 1)
            counts, actual_edges = np.histogram(values, bins=edges)
            width = float(actual_edges[1] - actual_edges[0])

            rows = []
            for idx, count in enumerate(counts):
                if count <= 0:
                    continue
                left = float(actual_edges[idx])
                right = float(actual_edges[idx + 1])
                rows.append({
                    "center": (left + right) / 2,
                    "width": width,
                    "pct": count / pnl_values.size * 100,
                    "color": color,
                    "customdata": [left, right, int(count)],
                })
            return rows

        rows = []
        rows.extend(_build_side_bins(pnl_values[pnl_values < 0], "loss"))
        rows.extend(_build_side_bins(pnl_values[pnl_values > 0], "profit"))

        zero_count = int(np.sum(pnl_values == 0))
        if zero_count > 0:
            widths = [row["width"] for row in rows]
            zero_width = min(widths) if widths else 1.0
            rows.append({
                "center": 0.0,
                "width": zero_width,
                "pct": zero_count / pnl_values.size * 100,
                "color": "#9ca3af",
                "customdata": [0.0, 0.0, zero_count],
            })

        rows.sort(key=lambda row: row["center"])

        fig = go.Figure(data=[go.Bar(
            x=[row["center"] for row in rows],
            y=[row["pct"] for row in rows],
            width=[row["width"] for row in rows],
            name='交易占比',
            marker_color=[row["color"] for row in rows],
            customdata=[row["customdata"] for row in rows],
            hovertemplate=(
                "\u51c0\u76c8\u4e8f\u533a\u95f4: \u00a5%{customdata[0]:,.0f} \u81f3 \u00a5%{customdata[1]:,.0f}<br>"
                "交易笔数: %{customdata[2]}<br>"
                "交易占比: %{y:.2f}%<extra></extra>"
            ),
        )])

        fig.update_layout(
            height=350, margin=dict(l=10, r=10, t=30, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(
                title=dict(text="\u9010\u7b14\u51c0\u76c8\u4e8f\u533a\u95f4 (\u00a5)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                zeroline=False
            ),
            yaxis=dict(
                title=dict(text="交易占比 (%)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                ticksuffix='%',
                showgrid=True,
                gridcolor='#f3f4f6'
            )
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_period_returns_html_div(self):
        """\u751f\u6210\u591a\u5468\u671f\u6536\u76ca\u6761\u5f62\u56fe\uff0c\u652f\u6301\u5468\u3001\u6708\u3001\u5e74\u5207\u6362\u3002"""
        if getattr(self, 'equity_df', None) is None or self.equity_df.empty:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u8d44\u91d1\u6570\u636e</div>"

        df_eq = self.equity_df.copy()
        df_eq['datetime'] = pd.to_datetime(df_eq['datetime'])
        df_eq = df_eq.sort_values('datetime').drop_duplicates('datetime', keep='last')
        df_eq.set_index('datetime', inplace=True)

        def _calc_returns(resample_rule, date_format):
            eq_series = df_eq['equity'].resample(resample_rule).last().dropna()
            returns, labels, colors = [], [], []
            prev_eq = self.initial_capital
            for dt, eq in eq_series.items():
                ret = (eq - prev_eq) / prev_eq if prev_eq > 0 else 0.0
                returns.append(ret)
                labels.append(dt.strftime(date_format))
                colors.append('#dc2626' if ret > 0 else '#16a34a')
                prev_eq = eq
            return labels, returns, colors

        # 周收益使用标准日期字符串，避�?Plotly 把周标�?当作不可解析类别�?
        w_labels, w_rets, w_colors = _calc_returns('W-FRI', '%Y-%m-%d')
        m_labels, m_rets, m_colors = _calc_returns('ME', '%Y-%m')
        y_labels, y_rets, y_colors = _calc_returns('YE', '%Y')

        fig = go.Figure()
        fig.add_trace(go.Bar(x=w_labels, y=w_rets, marker_color=w_colors, visible=False, name='\u5468\u6536\u76ca',
                             hovertemplate="\u5f53\u5468\u5468\u4e94: %{x}<br>\u6536\u76ca\u7387: %{y:.2%}<extra></extra>"))
        fig.add_trace(go.Bar(x=m_labels, y=m_rets, marker_color=m_colors, visible=True, name='\u6708\u6536\u76ca',
                             hovertemplate="\u6708\u4efd: %{x}<br>\u6536\u76ca\u7387: %{y:.2%}<extra></extra>"))
        fig.add_trace(go.Bar(x=y_labels, y=y_rets, marker_color=y_colors, visible=False, name='\u5e74\u6536\u76ca',
                             hovertemplate="\u5e74\u4efd: %{x}<br>\u6536\u76ca\u7387: %{y:.2%}<extra></extra>"))

        # �?�?年切换按�?��在图表左上�?，避�?右上�?modebar�?
        fig.update_layout(
            updatemenus=[dict(
                type="buttons", direction="right", active=1,
                x=0.0, y=1.15, xanchor="left", yanchor="top",  # 坐标定在左上
                buttons=list([
                    dict(label="\u5468\u6536\u76ca (Weekly)", method="update", args=[{"visible": [True, False, False]}]),
                    dict(label="\u6708\u6536\u76ca (Monthly)", method="update", args=[{"visible": [False, True, False]}]),
                    dict(label="\u5e74\u6536\u76ca (Yearly)", method="update", args=[{"visible": [False, False, True]}])
                ]),
                pad={"r": 10, "t": 10}, showactive=True, bgcolor="#f3f4f6", bordercolor="#d1d5db"
            )],
            height=350, margin=dict(l=10, r=10, t=50, b=10),  # 增加顶部边距给按�?��出空�?
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', hovermode="x unified",
            yaxis=dict(title=dict(text="\u533a\u95f4\u6536\u76ca\u7387", font=dict(color="#1f2937")), tickformat=".1%", showgrid=True,
                       gridcolor='#f3f4f6'),
            xaxis=dict(tickfont=dict(size=10), tickangle=-45)
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_replay_charts_dict(self):
        """\u751f\u6210\u5355\u54c1\u79cd\u4ef7\u683c\u66f2\u7ebf\u4e0e\u4e70\u5356\u70b9\u590d\u76d8\u56fe HTML Div \u5b57\u5178\u3002"""
        if not self.trades or getattr(self, 'price_df', None) is None or self.price_df.empty:
            return {}

        replay_dicts = {}
        # 提取�?有交易过的品种，去重并排�?
        traded_symbols = sorted(list(set([t.symbol.lower() for t in self.trades])))

        # 日期�?���??�列�?��避免 Plotly �?pandas 索引当作价格序列�?
        if 'datetime' in self.price_df.columns:
            dates = pd.to_datetime(self.price_df['datetime']).tolist()
        else:
            dates = pd.to_datetime(self.price_df.index).tolist()

        for sym in traded_symbols:
            # 精确找到当前品�?的价格列，只允�? close / last_price 等价格字段�??
            # 不能在整�??表里模糊匹配，否则�?易把 volume/open_interest 当成价格线�??
            candidates = []
            for col in self.price_df.columns:
                if col == 'datetime':
                    continue

                field = _price_field_from_column(col)
                if field not in PRICE_FIELD_PRIORITY:
                    continue

                keys = _symbol_price_lookup_keys(_symbol_label_from_column(col))
                if sym in keys:
                    exact_rank = 0 if keys and keys[0] == sym else 1
                    candidates.append((exact_rank, PRICE_FIELD_PRIORITY.index(field), col, field))

            if candidates:
                _, _, sym_col, price_field = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
            else:
                sym_col = None
                price_field = 'close'

            if sym_col is None:
                continue

            # 价格列强制转�?float，避�?pd.NA �?Plotly �?��为类�?��行号序列�?
            prices = pd.to_numeric(self.price_df[sym_col], errors='coerce').tolist()
            plot_dates, plot_prices = self._daily_last_xy_for_report(dates, prices, "price")
            price_label = _price_field_label(price_field)
            sym_label = sym.upper()

            fig = go.Figure()
            # 1. 绘制底层价格主线�?
            fig.add_trace(go.Scatter(
                x=plot_dates, y=plot_prices, mode='lines', name=f'{sym_label} {price_label}',
                line=dict(color='#9ca3af', width=1.5),
                connectgaps=True,  # 忽略缺失值断�?
                hovertemplate=f"\u54c1\u79cd: {sym_label}<br>\u65f6\u95f4: %{{x}}<br>{price_label}: \u00a5%{{y:,.0f}}<extra></extra>"
            ))

            # 2. 提取当前品�?的开平仓坐标
            sym_trades = [t for t in self.trades if t.symbol.lower() == sym]

            ol_x, ol_y, ol_text = [], [], []  # \u5f00\u591a
            cl_x, cl_y, cl_text = [], [], []  # \u5e73\u591a
            os_x, os_y, os_text = [], [], []  # \u5f00\u7a7a
            cs_x, cs_y, cs_text = [], [], []  # 平空

            for t in sym_trades:

                txt = f"\u624b\u6570: {t.volume}<br>\u6210\u4ea4\u4ef7: \u00a5{t.price:,.0f}"

                # Direction 表示订单买卖方向，Offset 表示�?�?平仓�?���?

                # 1. 买入�?�?(Buy Open) = �?�?
                if t.direction == Direction.LONG and t.offset == Offset.OPEN:
                    ol_x.append(t.trade_time);
                    ol_y.append(float(t.price));
                    ol_text.append(txt)

                # 2. 卖出平仓 (Sell Close) = 平�?
                elif t.direction == Direction.SHORT and t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]:
                    cl_x.append(t.trade_time);
                    cl_y.append(float(t.price));
                    cl_text.append(txt)

                # 3. 卖出�?�?(Sell Open) = �?�?
                elif t.direction == Direction.SHORT and t.offset == Offset.OPEN:
                    os_x.append(t.trade_time);
                    os_y.append(float(t.price));
                    os_text.append(txt)

                # 4. 买入平仓 (Buy Close) = 平空
                elif t.direction == Direction.LONG and t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]:
                    cs_x.append(t.trade_time);
                    cs_y.append(float(t.price));
                    cs_text.append(txt)
            # 3. 绘制买卖点图�?(极其符合国内投研直�?的图�?
            if ol_x:
                # �?�?(买入�?�? -> 红色实心正三�?
                fig.add_trace(go.Scatter(x=ol_x, y=ol_y, mode='markers', name='\u5f00\u591a (Open Long)',
                                         marker=dict(symbol='triangle-up', size=13, color='#dc2626',
                                                     line=dict(width=1, color='white')),
                                         text=ol_text,
                                         hovertemplate="<b>\u3010\u5f00\u591a\u3011</b><br>\u65f6\u95f4: %{x}<br>%{text}<extra></extra>"))
            if cl_x:
                # 平�? (卖出平仓) -> 绿色空心正方�?
                fig.add_trace(go.Scatter(x=cl_x, y=cl_y, mode='markers', name='\u5e73\u591a (Close Long)',
                                         marker=dict(symbol='square-open', size=11, color='#16a34a',
                                                     line=dict(width=2.5)),
                                         text=cl_text,
                                         hovertemplate="<b>\u3010\u5e73\u591a\u3011</b><br>\u65f6\u95f4: %{x}<br>%{text}<extra></extra>"))
            if os_x:
                # �?�?(卖出�?�? -> 绿色实心倒三�?
                fig.add_trace(go.Scatter(x=os_x, y=os_y, mode='markers', name='\u5f00\u7a7a (Open Short)',
                                         marker=dict(symbol='triangle-down', size=13, color='#16a34a',
                                                     line=dict(width=1, color='white')),
                                         text=os_text,
                                         hovertemplate="<b>\u3010\u5f00\u7a7a\u3011</b><br>\u65f6\u95f4: %{x}<br>%{text}<extra></extra>"))
            if cs_x:
                # 平空 (买入平仓) -> 红色空心正方�?
                fig.add_trace(go.Scatter(x=cs_x, y=cs_y, mode='markers', name='平空 (Close Short)',
                                         marker=dict(symbol='square-open', size=11, color='#dc2626',
                                                     line=dict(width=2.5)),
                                         text=cs_text,
                                         hovertemplate="<b>\u3010\u5e73\u7a7a\u3011</b><br>\u65f6\u95f4: %{x}<br>%{text}<extra></extra>"))

            fig.update_layout(
                title=dict(
                    text=f"{sym_label} \u4ea4\u6613\u590d\u76d8 | \u4ef7\u683c: {price_label}",
                    x=0.01,
                    xanchor='left',
                    font=dict(size=16, color='#1f2937')
                ),
                height=520, margin=dict(l=10, r=10, t=50, b=10),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', hovermode="closest",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                yaxis=dict(title=dict(text=f"{sym_label} {price_label}", font=dict(color="#1f2937")), tickfont=dict(color="#1f2937"),
                           showgrid=True, gridcolor='#f3f4f6'),
                xaxis=dict(
                    showgrid=False,
                    tickfont=dict(color="#1f2937"),
                    rangebreaks=[
                        dict(bounds=["sat", "mon"]),
                        dict(pattern="hour", bounds=[2.5, 9]),
                        dict(pattern="hour", bounds=[10.25, 10.5]),
                        dict(pattern="hour", bounds=[11.5, 13.5]),
                        dict(pattern="hour", bounds=[15, 21]),
                    ],
                )
            )

            replay_dicts[sym.upper()] = fig.to_html(full_html=False, include_plotlyjs=False)

        return replay_dicts


    def get_fund_flow_df(self):
        """生成资金流表 DataFrame (#3)"""
        if getattr(self, 'equity_df', None) is None or self.equity_df.empty:
            return pd.DataFrame()

        df_eq = self.equity_df.copy()
        df_eq['datetime'] = pd.to_datetime(df_eq['datetime'])
        df_eq = df_eq.sort_values('datetime')
        # 手续费必须按真实成交时间�??。不能先按日期汇总后 merge �?tick 资金�?��
        # 否则同一天的手续费会在每�?�?tick 权益记录上�?重�? cumsum�?
        commission_events = self._get_commission_events()
        if not commission_events.empty:
            commission_timeline = (
                commission_events
                .sort_values('datetime')[['datetime', 'commission']]
                .assign(cumulative_commission=lambda df: df['commission'].cumsum())
                [['datetime', 'cumulative_commission']]
            )
            df_eq = pd.merge_asof(
                df_eq,
                commission_timeline,
                on='datetime',
                direction='backward',
            )
            df_eq['cumulative_commission'] = df_eq['cumulative_commission'].fillna(0.0)
        else:
            df_eq['cumulative_commission'] = 0.0

        position_notional = (
            pd.to_numeric(df_eq['position_notional'], errors='coerce').fillna(0.0).round(2)
            if 'position_notional' in df_eq.columns else 0.0
        )

        df_res = pd.DataFrame({
            '\u65f6\u95f4': df_eq['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S'),
            '\u52a8\u6001\u6743\u76ca': df_eq['equity'].round(2),
            '\u6301\u4ed3\u540d\u4e49\u672c\u91d1': position_notional,
            '\u7d2f\u8ba1\u76c8\u4e8f': (df_eq['equity'] - self.initial_capital).round(2),
            '\u7d2f\u8ba1\u624b\u7eed\u8d39': df_eq['cumulative_commission'].round(2),
        })
        return df_res

    def _signal_horizons(self) -> list[int]:
        if self.freq == "1d":
            return [1, 3, 5, 10, 20]
        if self.freq == "tick":
            return [1, 10, 50, 100, 500]
        return [1, 3, 6, 12, 24]

    def _get_signal_price_series_map(self) -> dict[str, pd.Series]:
        if hasattr(self, "_signal_price_series_cache"):
            return self._signal_price_series_cache

        series_map = {}
        if getattr(self, "price_df", None) is None or self.price_df.empty:
            self._signal_price_series_cache = series_map
            return series_map

        if "datetime" in self.price_df.columns:
            dt_index = pd.to_datetime(self.price_df["datetime"], errors="coerce")
        else:
            dt_index = pd.to_datetime(self.price_df.index, errors="coerce")

        for col in self.price_df.columns:
            if col == "datetime":
                continue
            field = _price_field_from_column(col)
            if field not in PRICE_FIELD_PRIORITY:
                continue

            lookup_keys = _symbol_price_lookup_keys(_symbol_label_from_column(col))
            values = pd.to_numeric(self.price_df[col], errors="coerce")
            series = pd.Series(values.to_numpy(), index=dt_index).dropna()
            if series.empty:
                continue
            series = series[~series.index.duplicated(keep="last")].sort_index()

            priority = PRICE_FIELD_PRIORITY.index(field)
            for code in lookup_keys:
                existing = series_map.get(code)
                if existing is None or priority < existing[0]:
                    series_map[code] = (priority, series)

        self._signal_price_series_cache = {code: item[1] for code, item in series_map.items()}
        return self._signal_price_series_cache

    def _raw_signal_events_df(self) -> pd.DataFrame:
        if not self.signal_records:
            return pd.DataFrame()

        df = pd.DataFrame(self.signal_records)
        if df.empty or "datetime" not in df.columns or "symbol" not in df.columns:
            return pd.DataFrame()

        from config import trade_symbol_code

        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime", "symbol"])
        df["symbol"] = df["symbol"].map(lambda value: trade_symbol_code(str(value)).lower())
        df["signal"] = pd.to_numeric(df.get("signal"), errors="coerce")
        df["signal"] = self._infer_effective_signal(df)
        df["signal_score"] = self._infer_signal_score(df)
        df = df.sort_values(["datetime", "symbol"]).reset_index(drop=True)
        return df

    @staticmethod
    def _infer_effective_signal(df: pd.DataFrame) -> pd.Series:
        """Infer signal direction for target-style intents that omit signal."""
        signal = pd.to_numeric(df.get("signal"), errors="coerce")

        def fill_from_signed_column(column: str):
            nonlocal signal
            if column not in df.columns:
                return
            values = pd.to_numeric(df[column], errors="coerce")
            missing = signal.isna() & values.notna()
            signal.loc[missing & (values > 0)] = 1
            signal.loc[missing & (values < 0)] = -1
            signal.loc[missing & (values == 0)] = 0

        fill_from_signed_column("target_net")
        fill_from_signed_column("target_weight")
        fill_from_signed_column("target_margin_pct")

        if "position_mode" in df.columns:
            mode = df["position_mode"].astype(str).str.lower()
            exit_mask = signal.isna() & mode.isin(["flat", "reduce", "exit", "close", "all_flat", "partial_close"])
            signal.loc[exit_mask] = 0

        return signal

    @staticmethod
    def _numeric_signal_column(df: pd.DataFrame, column: str) -> pd.Series:
        if column not in df.columns:
            return pd.Series(np.nan, index=df.index, dtype=float)
        return pd.to_numeric(df[column], errors="coerce")

    @staticmethod
    def _infer_signal_score(df: pd.DataFrame) -> pd.Series:
        """
        Build a signed signal score for IC/Rank IC.

        Explicit signed target fields are used first. For absolute sizing fields
        such as size_scale or risk_pct, the effective signal direction supplies
        the sign. If no strength field exists, the score falls back to -1/0/1.
        """
        signal = pd.to_numeric(df.get("signal"), errors="coerce")
        explicit_score = StrategyAnalyzer._numeric_signal_column(df, "signal_score")
        score = explicit_score.copy()
        score.loc[score.isna()] = signal.loc[score.isna()]

        for column in ("target_weight", "target_margin_pct", "target_net"):
            values = StrategyAnalyzer._numeric_signal_column(df, column)
            mask = values.notna() & explicit_score.isna()
            score.loc[mask] = values.loc[mask]

        for column in ("target_pct", "size_scale", "risk_pct", "target_volume", "delta_volume"):
            values = StrategyAnalyzer._numeric_signal_column(df, column)
            mask = values.notna() & signal.notna() & explicit_score.isna()
            score.loc[mask] = signal.loc[mask] * values.loc[mask].abs()

        return score

    def _build_signal_diagnostics_df(self) -> pd.DataFrame:
        if hasattr(self, "_signal_diagnostics_cache"):
            return self._signal_diagnostics_cache.copy()

        df = self._raw_signal_events_df()
        if df.empty:
            self._signal_diagnostics_cache = pd.DataFrame()
            return pd.DataFrame()

        horizons = self._signal_horizons()
        for horizon in horizons:
            df[f"fwd_{horizon}_bar_return"] = np.nan
            df[f"fwd_{horizon}_bar_raw_return"] = np.nan
        df["fwd_mfe_24_bar"] = np.nan
        df["fwd_mae_24_bar"] = np.nan

        price_map = self._get_signal_price_series_map()
        entry_mask = df["signal"].isin([1, -1])

        for sym, group in df.loc[entry_mask].groupby("symbol"):
            price_series = price_map.get(sym)
            if price_series is None or price_series.empty:
                continue

            signal_times = pd.to_datetime(group["datetime"])
            indexer = price_series.index.get_indexer(signal_times)
            missing = indexer < 0
            if missing.any():
                fallback = price_series.index.get_indexer(signal_times[missing], method="ffill")
                indexer[missing] = fallback

            prices = price_series.to_numpy(dtype=float)
            valid_base = (indexer >= 0) & (indexer < len(prices)) & (prices[indexer] > 0)
            if not valid_base.any():
                continue

            row_positions = group.index.to_numpy()
            directions = group["signal"].to_numpy(dtype=float)

            for horizon in horizons:
                future_pos = indexer + horizon
                valid = valid_base & (future_pos < len(prices))
                if not valid.any():
                    continue
                raw_ret = prices[future_pos[valid]] / prices[indexer[valid]] - 1.0
                directional_ret = directions[valid] * raw_ret
                df.loc[row_positions[valid], f"fwd_{horizon}_bar_raw_return"] = raw_ret
                df.loc[row_positions[valid], f"fwd_{horizon}_bar_return"] = directional_ret

            mfe_values = []
            mae_values = []
            for pos, direction, valid in zip(indexer, directions, valid_base):
                if not valid:
                    mfe_values.append(np.nan)
                    mae_values.append(np.nan)
                    continue
                end_pos = min(len(prices), int(pos) + 25)
                path = prices[int(pos):end_pos]
                path_ret = direction * (path / prices[int(pos)] - 1.0)
                mfe_values.append(float(np.nanmax(path_ret)) if len(path_ret) else np.nan)
                mae_values.append(float(np.nanmin(path_ret)) if len(path_ret) else np.nan)

            df.loc[group.index, "fwd_mfe_24_bar"] = mfe_values
            df.loc[group.index, "fwd_mae_24_bar"] = mae_values

        self._signal_diagnostics_cache = df
        return df.copy()

    @staticmethod
    def _fmt_pct_value(value) -> str:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value) * 100:.3f}%"

    @staticmethod
    def _fmt_number_value(value, decimals: int = 2) -> str:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.{decimals}f}"

    @staticmethod
    def _safe_corr(x, y, method: str = "pearson", min_count: int = 3) -> float:
        data = pd.DataFrame({
            "x": pd.to_numeric(x, errors="coerce"),
            "y": pd.to_numeric(y, errors="coerce"),
        }).dropna()
        if len(data) < min_count:
            return np.nan
        if data["x"].nunique() <= 1 or data["y"].nunique() <= 1:
            return np.nan
        return float(data["x"].corr(data["y"], method=method))

    @staticmethod
    def _fmt_ic_value(value) -> str:
        if value is None or pd.isna(value):
            return "-"
        return f"{float(value):.3f}"

    def _build_signal_ic_df(self, entry_df: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
        rows = []
        if entry_df is None or entry_df.empty or "signal_score" not in entry_df.columns:
            return pd.DataFrame(columns=["horizon", "ic", "rank_ic", "count"])

        for horizon in horizons:
            raw_col = f"fwd_{horizon}_bar_raw_return"
            if raw_col not in entry_df.columns:
                rows.append({"horizon": f"T+{horizon}", "ic": np.nan, "rank_ic": np.nan, "count": 0})
                continue

            sample = entry_df[["signal_score", raw_col]].dropna()
            rows.append({
                "horizon": f"T+{horizon}",
                "ic": self._safe_corr(sample["signal_score"], sample[raw_col], method="pearson"),
                "rank_ic": self._safe_corr(sample["signal_score"], sample[raw_col], method="spearman"),
                "count": int(len(sample)),
            })

        return pd.DataFrame(rows)

    def _build_group_ic_df(self, entry_df: pd.DataFrame, group_col: str, raw_col: str) -> pd.DataFrame:
        rows = []
        if entry_df is None or entry_df.empty or group_col not in entry_df.columns or raw_col not in entry_df.columns:
            return pd.DataFrame(columns=[group_col, "ic", "rank_ic", "count"])

        for group_value, group in entry_df.groupby(group_col):
            sample = group[["signal_score", raw_col]].dropna()
            rows.append({
                group_col: group_value,
                "ic": self._safe_corr(sample["signal_score"], sample[raw_col], method="pearson"),
                "rank_ic": self._safe_corr(sample["signal_score"], sample[raw_col], method="spearman"),
                "count": int(len(sample)),
            })
        return pd.DataFrame(rows)

    def _build_score_quantile_df(self, entry_df: pd.DataFrame, raw_col: str, buckets: int = 5) -> pd.DataFrame:
        if entry_df is None or entry_df.empty or "signal_score" not in entry_df.columns or raw_col not in entry_df.columns:
            return pd.DataFrame()

        sample = entry_df[["signal_score", raw_col]].dropna().copy()
        if len(sample) < buckets or sample["signal_score"].nunique() < 3:
            return pd.DataFrame()

        try:
            sample["score_bucket"] = pd.qcut(sample["signal_score"], q=buckets, duplicates="drop")
        except ValueError:
            return pd.DataFrame()

        if sample["score_bucket"].nunique() < 2:
            return pd.DataFrame()

        result = sample.groupby("score_bucket", observed=True).agg(
            sample_count=("signal_score", "size"),
            avg_signal_score=("signal_score", "mean"),
            avg_raw_return=(raw_col, "mean"),
            win_rate=(raw_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
        ).reset_index()
        result["\u5206\u7ec4"] = [f"Q{i + 1}" for i in range(len(result))]
        return result[["\u5206\u7ec4", "sample_count", "avg_signal_score", "avg_raw_return", "win_rate"]].rename(columns={"sample_count": "\u6837\u672c\u6570", "avg_signal_score": "\u5e73\u5747\u4fe1\u53f7\u5206\u6570", "avg_raw_return": "\u5e73\u5747\u539f\u59cb\u6536\u76ca", "win_rate": "\u80dc\u7387"})

    @staticmethod
    def _compact_table_html(df: pd.DataFrame) -> str:
        if df is None or df.empty:
            return "<div class='text-center text-gray-500 py-10'>暂无数据</div>"
        return df.to_html(
            index=False, border=0,
            classes="w-full text-xs text-center text-gray-700 bg-white"
        ).replace("<thead>", '<thead class="bg-gray-100 text-gray-700 sticky top-0">') \
         .replace("<th>", '<th class="py-2 px-3 text-center whitespace-nowrap">') \
         .replace("<td>", '<td class="py-2 px-3 text-center border-b border-gray-50">') \
         .replace('style="text-align: right;"', '')

    def _signal_rebalance_summary(self) -> dict:
        if not self.rebalance_records:
            return {"submitted": 0, "no_order": 0, "hold": 0}
        df = pd.DataFrame(self.rebalance_records)
        if df.empty or "action" not in df.columns:
            return {"submitted": 0, "no_order": 0, "hold": 0}
        counts = df["action"].value_counts()
        return {
            "submitted": int(counts.get("order_submitted", 0)),
            "no_order": int(counts.get("no_order", 0)),
            "hold": int(counts.get("hold", 0)),
        }

    def get_signal_diagnostics_html_div(self):
        return self._get_signal_diagnostics_interactive_html_div()

    def _get_signal_diagnostics_interactive_html_div(self):
        df = self._build_signal_diagnostics_df()
        if df.empty:
            stale_path = os.path.join(self.output_dir, "signal_events_full.csv")
            if os.path.exists(stale_path):
                os.remove(stale_path)
            return """
            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-8 text-center text-gray-500">
                \u672c\u6b21\u56de\u6d4b\u6ca1\u6709\u8bb0\u5f55\u5230\u53ef\u89c2\u6d4b\u4fe1\u53f7\u3002\u8bf7\u786e\u8ba4\u7b56\u7565\u7ee7\u627f GeneralSignalStrategy\uff0c\u5e76\u542f\u7528 record_signals=True\u3002
            </div>
            """

        from config import NAME_TO_CODE, pure_product_code

        df = df.copy()
        df["product"] = df["symbol"].map(lambda value: pure_product_code(str(value)).upper())
        df["sector"] = df["symbol"].map(_symbol_sector)

        name_by_product = {}
        for name, code in NAME_TO_CODE.items():
            product = pure_product_code(str(code)).upper()
            if product not in name_by_product or len(str(name)) < len(name_by_product[product]):
                name_by_product[product] = str(name)

        def product_label(product: str) -> str:
            product = str(product).upper()
            name = name_by_product.get(product)
            return f"{name} {product}" if name else product

        csv_name = "signal_events_full.csv"
        df.to_csv(os.path.join(self.output_dir, csv_name), index=False, encoding="utf-8-sig")

        horizons = self._signal_horizons()
        primary_horizon = 6 if 6 in horizons else horizons[min(2, len(horizons) - 1)]
        primary_col = f"fwd_{primary_horizon}_bar_return"
        primary_raw_col = f"fwd_{primary_horizon}_bar_raw_return"

        def clean_value(value):
            if value is None:
                return None
            if isinstance(value, (np.integer,)):
                return int(value)
            if isinstance(value, (np.floating,)):
                value = float(value)
            if isinstance(value, float):
                return value if np.isfinite(value) else None
            if isinstance(value, pd.Timestamp):
                return _format_time_label(value)
            if pd.isna(value):
                return None
            return value

        def clean_int(value, default: int = 0) -> int:
            if value is None or pd.isna(value):
                return default
            return int(value)

        def fmt_pct(value) -> str:
            return self._fmt_pct_value(value)

        def fmt_number(value, decimals: int = 2) -> str:
            return self._fmt_number_value(value, decimals)

        def build_cards(scope_df: pd.DataFrame, entry_df: pd.DataFrame, ic_df: pd.DataFrame) -> list[dict]:
            exit_count = int((scope_df["signal"] == 0).sum())
            long_count = int((scope_df["signal"] == 1).sum())
            short_count = int((scope_df["signal"] == -1).sum())
            valid_primary = entry_df[primary_col].dropna() if primary_col in entry_df.columns else pd.Series(dtype=float)
            win_rate = float((valid_primary > 0).mean()) if not valid_primary.empty else np.nan
            avg_forward = float(valid_primary.mean()) if not valid_primary.empty else np.nan
            primary_ic_row = ic_df.loc[ic_df["horizon"] == f"T+{primary_horizon}"] if not ic_df.empty else pd.DataFrame()
            primary_ic = float(primary_ic_row["ic"].iloc[0]) if not primary_ic_row.empty else np.nan
            primary_rank_ic = float(primary_ic_row["rank_ic"].iloc[0]) if not primary_ic_row.empty else np.nan
            balance_ratio = min(long_count, short_count) / max(long_count, short_count) if max(long_count, short_count) > 0 else np.nan
            avg_mfe = float(entry_df["fwd_mfe_24_bar"].mean()) if "fwd_mfe_24_bar" in entry_df and not entry_df.empty else np.nan
            avg_mae = float(entry_df["fwd_mae_24_bar"].mean()) if "fwd_mae_24_bar" in entry_df and not entry_df.empty else np.nan
            mfe_mae_ratio = avg_mfe / abs(avg_mae) if pd.notna(avg_mfe) and pd.notna(avg_mae) and abs(avg_mae) > 1e-12 else np.nan
            return [
                {"title": "信号总数", "value": f"{len(scope_df):,}", "subtitle": "Recorded signal events"},
                {"title": "开仓信号", "value": f"{len(entry_df):,}", "subtitle": f"Long {long_count:,} / Short {short_count:,}"},
                {"title": "平仓信号", "value": f"{exit_count:,}", "subtitle": "Exit / Flat signals"},
                {"title": f"T+{primary_horizon} Bar 胜率", "value": fmt_pct(win_rate), "subtitle": "Directional hit rate"},
                {"title": f"T+{primary_horizon} Bar 均值", "value": fmt_pct(avg_forward), "subtitle": "Average directional return"},
                {"title": f"T+{primary_horizon} IC", "value": self._fmt_ic_value(primary_ic), "subtitle": "Score vs raw forward return"},
                {"title": f"T+{primary_horizon} Rank IC", "value": self._fmt_ic_value(primary_rank_ic), "subtitle": "Rank correlation"},
                {"title": "多空均衡度", "value": fmt_pct(balance_ratio), "subtitle": "Min(long, short) / max(long, short)"},
                {"title": "MFE/MAE", "value": fmt_number(mfe_mae_ratio, 2), "subtitle": "Avg favorable / adverse excursion"},
            ]

        def build_avg_rows(entry_df: pd.DataFrame) -> list[dict]:
            rows = []
            for horizon in horizons:
                col = f"fwd_{horizon}_bar_return"
                sample = entry_df[col].dropna() if col in entry_df.columns else pd.Series(dtype=float)
                rows.append({
                    "horizon": f"T+{horizon}",
                    "avg_return_pct": float(sample.mean() * 100) if not sample.empty else None,
                    "win_rate_pct": float((sample > 0).mean() * 100) if not sample.empty else None,
                    "count": int(sample.count()),
                })
            return rows

        def build_distribution(entry_df: pd.DataFrame) -> dict:
            sample = entry_df[primary_col].dropna() * 100 if primary_col in entry_df.columns else pd.Series(dtype=float)
            if sample.empty:
                return {"x": [], "y": [], "width": []}
            bins = min(40, max(8, int(np.sqrt(len(sample))) if len(sample) > 0 else 8))
            counts, edges = np.histogram(sample.to_numpy(dtype=float), bins=bins)
            centers = (edges[:-1] + edges[1:]) / 2
            widths = edges[1:] - edges[:-1]
            return {
                "x": [clean_value(item) for item in centers],
                "y": [int(item) for item in counts],
                "width": [clean_value(item) for item in widths],
            }

        def build_group_rows(entry_df: pd.DataFrame, group_col: str) -> list[dict]:
            if entry_df.empty or group_col not in entry_df.columns:
                return []
            grouped = entry_df.groupby(group_col).agg(
                signal_count=("signal", "size"),
                long_count=("signal", lambda s: int((s == 1).sum())),
                short_count=("signal", lambda s: int((s == -1).sum())),
                avg_return=(primary_col, "mean"),
                win_rate=(primary_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
                avg_mfe=("fwd_mfe_24_bar", "mean"),
                avg_mae=("fwd_mae_24_bar", "mean"),
            ).reset_index()
            group_ic_df = self._build_group_ic_df(entry_df, group_col, primary_raw_col)
            if not group_ic_df.empty:
                grouped = grouped.merge(group_ic_df[[group_col, "ic", "rank_ic", "count"]], on=group_col, how="left")
            else:
                grouped["ic"] = np.nan
                grouped["rank_ic"] = np.nan
                grouped["count"] = 0

            rows = []
            for _, row in grouped.sort_values("signal_count", ascending=False).iterrows():
                key = str(row[group_col])
                display = product_label(key) if group_col == "product" else key
                rows.append({
                    "分组": display,
                    "信号数": int(row["signal_count"]),
                    "多头": int(row["long_count"]),
                    "空头": int(row["short_count"]),
                    "平均收益": fmt_pct(row["avg_return"]),
                    "胜率": fmt_pct(row["win_rate"]),
                    "平均MFE": fmt_pct(row["avg_mfe"]),
                    "平均MAE": fmt_pct(row["avg_mae"]),
                    "IC": self._fmt_ic_value(row.get("ic")),
                    "Rank IC": self._fmt_ic_value(row.get("rank_ic")),
                    "IC样本": clean_int(row.get("count", 0)),
                })
            return rows

        def build_reason_rows(scope_df: pd.DataFrame) -> list[dict]:
            reason_df = (
                scope_df.fillna({"reason": ""})
                .assign(reason=lambda item: item["reason"].replace("", "unspecified"))
                .groupby(["reason", "signal"])
                .size()
                .reset_index(name="次数")
                .sort_values("次数", ascending=False)
                .head(50)
            )
            return [
                {"原因": str(row["reason"]), "信号": clean_value(row["signal"]), "次数": int(row["次数"])}
                for _, row in reason_df.iterrows()
            ]

        def build_month_rows(entry_df: pd.DataFrame) -> list[dict]:
            if entry_df.empty:
                return []
            month_df = entry_df.copy()
            month_df["月份"] = pd.to_datetime(month_df["datetime"]).dt.to_period("M").astype(str)
            month_perf = month_df.groupby("月份").agg(
                signal_count=("signal", "size"),
                avg_return=(primary_col, "mean"),
                win_rate=(primary_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
            ).reset_index().tail(24)
            month_ic = self._build_group_ic_df(month_df, "月份", primary_raw_col)
            if not month_ic.empty:
                month_perf = month_perf.merge(month_ic.rename(columns={"ic": "IC", "rank_ic": "Rank IC", "count": "IC样本"}), on="月份", how="left")
            rows = []
            for _, row in month_perf.iterrows():
                rows.append({
                    "月份": row["月份"],
                    "信号数": int(row["signal_count"]),
                    "平均收益": fmt_pct(row["avg_return"]),
                    "胜率": fmt_pct(row["win_rate"]),
                    "IC": self._fmt_ic_value(row.get("IC")),
                    "Rank IC": self._fmt_ic_value(row.get("Rank IC")),
                    "IC样本": clean_int(row.get("IC样本", 0)),
                })
            return rows

        def build_direction_rows(entry_df: pd.DataFrame) -> list[dict]:
            if entry_df.empty:
                return []
            direction_df = entry_df.copy()
            direction_df["方向"] = direction_df["signal"].map({1: "多头信号 (Long)", -1: "空头信号 (Short)"})
            stats = direction_df.groupby("方向").agg(
                signal_count=("signal", "size"),
                avg_return=(primary_col, "mean"),
                win_rate=(primary_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
                avg_mfe=("fwd_mfe_24_bar", "mean"),
                avg_mae=("fwd_mae_24_bar", "mean"),
            ).reset_index()
            return [{
                "方向": row["方向"],
                "信号数": int(row["signal_count"]),
                "平均收益": fmt_pct(row["avg_return"]),
                "胜率": fmt_pct(row["win_rate"]),
                "平均MFE": fmt_pct(row["avg_mfe"]),
                "平均MAE": fmt_pct(row["avg_mae"]),
            } for _, row in stats.iterrows()]

        def build_event_rows(scope_df: pd.DataFrame) -> list[dict]:
            columns = [
                "datetime", "sector", "product", "symbol", "signal", "reason", "current_net", "price",
                "signal_score", "size_scale", f"fwd_{horizons[0]}_bar_return",
                primary_col, primary_raw_col, "fwd_mfe_24_bar", "fwd_mae_24_bar",
            ]
            preview = scope_df[[col for col in columns if col in scope_df.columns]].copy()
            rows = []
            for _, row in preview.iterrows():
                item = {
                    "_sector": row.get("sector"),
                    "_product": row.get("product"),
                    "时间": _format_time_label(row.get("datetime")),
                    "板块": row.get("sector"),
                    "品种": product_label(row.get("product")),
                    "信号": clean_value(row.get("signal")),
                    "原因": row.get("reason") if pd.notna(row.get("reason")) else "",
                    "当时净持仓": clean_value(row.get("current_net")),
                    "信号价": fmt_number(row.get("price"), 4),
                    "信号分数": fmt_number(row.get("signal_score"), 4),
                    "仓位系数": fmt_number(row.get("size_scale"), 4),
                    f"T+{horizons[0]} Bar收益": fmt_pct(row.get(f"fwd_{horizons[0]}_bar_return")),
                    f"T+{primary_horizon} Bar收益": fmt_pct(row.get(primary_col)),
                    f"T+{primary_horizon} Bar原始收益": fmt_pct(row.get(primary_raw_col)),
                    "24 Bar MFE": fmt_pct(row.get("fwd_mfe_24_bar")),
                    "24 Bar MAE": fmt_pct(row.get("fwd_mae_24_bar")),
                }
                rows.append(item)
            return rows

        def build_scope(scope_df: pd.DataFrame, label: str, group_col: str, group_title: str) -> dict:
            scope_df = scope_df.sort_values(["datetime", "symbol"]).copy()
            entry_df = scope_df[scope_df["signal"].isin([1, -1])].copy()
            ic_df = self._build_signal_ic_df(entry_df, horizons)
            return {
                "label": label,
                "cards": build_cards(scope_df, entry_df, ic_df),
                "avgRows": build_avg_rows(entry_df),
                "distribution": build_distribution(entry_df),
                "icRows": [
                    {
                        "观察窗口": row["horizon"],
                        "IC": clean_value(row["ic"]),
                        "Rank IC": clean_value(row["rank_ic"]),
                        "样本数": int(row["count"]),
                    }
                    for _, row in ic_df.iterrows()
                ],
                "groupTitle": group_title,
                "groupRows": build_group_rows(entry_df, group_col),
                "directionRows": build_direction_rows(entry_df),
                "reasonRows": build_reason_rows(scope_df),
                "monthRows": build_month_rows(entry_df),
                "eventTotal": int(len(scope_df)),
                "entryTotal": int(len(entry_df)),
            }

        sectors = [sector for sector in SECTOR_ORDER if sector in set(df["sector"])]
        products = sorted(df["product"].dropna().unique().tolist(), key=_symbol_sort_key)
        product_options = [{"value": product, "label": product_label(product), "sector": _symbol_sector(product)} for product in products]
        sector_options = [{"value": sector, "label": sector} for sector in sectors]

        scopes = {"all": build_scope(df, "全部品种", "sector", "板块统计 (By Sector)")}
        for sector in sectors:
            subset = df[df["sector"] == sector]
            scopes[f"sector:{sector}"] = build_scope(subset, f"板块：{sector}", "product", "板块内品种统计 (Products in Sector)")
        for product in products:
            subset = df[df["product"] == product]
            scopes[f"product:{product}"] = build_scope(subset, f"品种：{product_label(product)}", "symbol", "合约/信号统计 (Contracts / Signals)")

        payload = {
            "csvName": csv_name,
            "primaryHorizon": primary_horizon,
            "firstHorizon": horizons[0],
            "sectors": sector_options,
            "products": product_options,
            "eventRows": build_event_rows(df),
            "scopes": scopes,
        }
        payload_json = json.dumps(payload, ensure_ascii=False, default=clean_value).replace("</", "<\\/")

        html = """
        <div class="space-y-6" id="signal-diagnostics-root">
            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                <div class="flex flex-col xl:flex-row xl:items-end xl:justify-between gap-4">
                    <div>
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">信号检测范围 (Signal Scope)</h2>
                        <p class="text-xs text-gray-500 mt-2 pl-3">选择全部、板块或具体品种后，下面的概览、IC、收益分布、统计表和事件明细会同步切换。</p>
                    </div>
                    <div class="flex flex-wrap items-end gap-3">
                        <div class="inline-flex rounded-lg border border-gray-200 overflow-hidden">
                            <button type="button" class="signal-scope-mode px-4 py-2 text-sm font-medium bg-[#1e3a8a] text-white" data-mode="all">全部品种</button>
                            <button type="button" class="signal-scope-mode px-4 py-2 text-sm font-medium bg-white text-gray-700 border-l border-gray-200" data-mode="sector">按板块</button>
                            <button type="button" class="signal-scope-mode px-4 py-2 text-sm font-medium bg-white text-gray-700 border-l border-gray-200" data-mode="product">按品种</button>
                        </div>
                        <label class="text-xs text-gray-500">
                            板块
                            <select id="signal-sector-select" class="block mt-1 min-w-[160px] rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700"></select>
                        </label>
                        <label class="text-xs text-gray-500">
                            品种
                            <select id="signal-product-select" class="block mt-1 min-w-[220px] rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700"></select>
                        </label>
                    </div>
                </div>
                <div id="signal-scope-label" class="mt-4 rounded-lg bg-cyan-50 px-4 py-3 text-sm font-medium text-cyan-900"></div>
            </div>

            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3 mb-4">信号检测概览 (Signal Inspection Overview)</h2>
                <div id="signal-card-grid" class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3"></div>
            </div>

            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3 mb-3">信号编码说明 (Signal Encoding)</h2>
                <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 text-sm text-gray-700">
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = 1</b><br>开多或加多头。是否成交取决于调仓器、保证金、挂单和撮合。</div>
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = -1</b><br>开空或加空头。原因字段记录策略为什么给出这个方向。</div>
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = 0</b><br>平仓或减仓信号。半平和全平都会表现为 0。</div>
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = None</b><br>观望或无动作信号，默认不展示，避免 hold / warming_up 淹没有效事件。</div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                <h2 class="text-lg font-bold text-gray-800 border-l-4 border-indigo-600 pl-3 mb-2">IC 检测 (Information Coefficient)</h2>
                <p class="text-xs text-gray-500 mb-3 pl-3">IC 使用信号分数与未来原始收益计算 Pearson 相关；Rank IC 使用 Spearman 秩相关。它衡量信号强弱排序是否对应未来涨跌，不等同于最终交易收益。</p>
                <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
                    <div id="signal-ic-chart" class="xl:col-span-2 min-h-[330px]"></div>
                    <div id="signal-ic-table" class="overflow-y-auto max-h-[330px]"></div>
                </div>
            </div>

            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3 mb-2">开仓信号后方向收益 (Entry Signal Forward Return)</h2>
                    <p class="text-xs text-gray-500 mb-2 pl-3">T+N 表示信号后第 N 根 Bar；红色为方向收益为正，绿色为方向收益为负。</p>
                    <div id="signal-forward-chart" class="min-h-[330px]"></div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-slate-600 pl-3 mb-2">开仓信号后收益分布 (Entry Signal Return Distribution)</h2>
                    <p class="text-xs text-gray-500 mb-2 pl-3">仅统计 signal=1/-1 的开仓方向信号；平仓信号不参与未来收益分布。</p>
                    <div id="signal-distribution-chart" class="min-h-[330px]"></div>
                </div>
            </div>

            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100"><h2 id="signal-group-title" class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3"></h2></div>
                    <div id="signal-group-table" class="overflow-y-auto max-h-[420px]"></div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100"><h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">多空方向表现 (By Direction)</h2></div>
                    <div id="signal-direction-table" class="overflow-y-auto max-h-[420px]"></div>
                </div>
            </div>

            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100"><h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">月份稳定性 (Monthly Stability)</h2></div>
                    <div id="signal-month-table" class="overflow-y-auto max-h-[420px]"></div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100"><h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">信号原因统计 (By Reason)</h2></div>
                    <div id="signal-reason-table" class="overflow-y-auto max-h-[420px]"></div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                <div class="p-4 border-b border-gray-100 bg-cyan-50 flex flex-col md:flex-row md:items-center md:justify-between gap-3">
                    <div>
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">信号事件明细 (Signal Events)</h2>
                        <p id="signal-event-note" class="text-xs text-gray-500 mt-2 pl-3"></p>
                    </div>
                    <a href="__CSV_NAME__" download class="bg-[#1e3a8a] hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium self-start md:self-auto">下载完整信号明细 CSV</a>
                </div>
                <div id="signal-event-table" class="overflow-y-auto max-h-[620px]"></div>
            </div>
        </div>
        <script>
        (function() {
            const payload = __SIGNAL_PAYLOAD__;
            const root = document.getElementById('signal-diagnostics-root');
            if (!root || !payload || !payload.scopes) return;

            const state = {
                mode: 'all',
                sector: (payload.sectors[0] || {}).value || '',
                product: (payload.products[0] || {}).value || ''
            };

            function escapeHtml(value) {
                if (value === null || value === undefined) return '-';
                return String(value).replace(/[&<>"']/g, function(ch) {
                    return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[ch];
                });
            }

            function setOptions(select, rows) {
                select.innerHTML = rows.map(function(row) {
                    return '<option value="' + escapeHtml(row.value) + '">' + escapeHtml(row.label) + '</option>';
                }).join('');
            }

            function currentScopeKey() {
                if (state.mode === 'sector') return 'sector:' + state.sector;
                if (state.mode === 'product') return 'product:' + state.product;
                return 'all';
            }

            function currentScope() {
                return payload.scopes[currentScopeKey()] || payload.scopes.all;
            }

            function emptyBox(text) {
                return '<div class="text-center text-gray-500 py-10">' + escapeHtml(text) + '</div>';
            }

            function tableHtml(rows) {
                if (!rows || !rows.length) return emptyBox('当前范围暂无可展示数据');
                const columns = Object.keys(rows[0]).filter(function(col) { return !col.startsWith('_'); });
                const header = columns.map(function(col) {
                    return '<th class="py-2 px-3 text-center whitespace-nowrap">' + escapeHtml(col) + '</th>';
                }).join('');
                const body = rows.map(function(row) {
                    return '<tr>' + columns.map(function(col) {
                        return '<td class="py-2 px-3 text-center border-b border-gray-50 whitespace-nowrap">' + escapeHtml(row[col]) + '</td>';
                    }).join('') + '</tr>';
                }).join('');
                return '<table class="w-full text-xs text-center text-gray-700 bg-white"><thead class="bg-gray-100 text-gray-700 sticky top-0"><tr>' + header + '</tr></thead><tbody>' + body + '</tbody></table>';
            }

            function renderCards(scope) {
                document.getElementById('signal-card-grid').innerHTML = (scope.cards || []).map(function(card) {
                    return '<div class="border border-gray-100 rounded-lg p-4 bg-gray-50">'
                        + '<div class="text-xs text-gray-500">' + escapeHtml(card.subtitle) + '</div>'
                        + '<div class="text-sm font-semibold text-gray-700 mt-1">' + escapeHtml(card.title) + '</div>'
                        + '<div class="text-2xl font-bold text-gray-900 mt-2">' + escapeHtml(card.value) + '</div>'
                        + '</div>';
                }).join('');
            }

            function signalTabIsVisible() {
                const tab = root.closest('.tab-content');
                return !tab || tab.classList.contains('active');
            }

            function safePlot(node, data, layout, config) {
                if (!window.Plotly) {
                    node.innerHTML = emptyBox('Plotly 未加载，无法绘制图表');
                    return;
                }
                Plotly.react(node, data, layout, config || {displayModeBar: false, responsive: true})
                    .then(function() {
                        try { Plotly.Plots.resize(node); } catch (err) {}
                    })
                    .catch(function(err) {
                        node.innerHTML = emptyBox('图表渲染失败：' + (err && err.message ? err.message : err));
                    });
            }

            let lastRenderedScope = null;
            let pendingChartRender = null;

            function scheduleCharts(scope) {
                lastRenderedScope = scope;
                if (!signalTabIsVisible()) return;
                if (pendingChartRender) cancelAnimationFrame(pendingChartRender);
                pendingChartRender = requestAnimationFrame(function() {
                    requestAnimationFrame(function() {
                        renderIcChart(scope);
                        renderForwardChart(scope);
                        renderDistribution(scope);
                    });
                });
            }

            function filteredEventRows() {
                const rows = payload.eventRows || [];
                let filtered = rows;
                if (state.mode === 'sector') {
                    filtered = rows.filter(function(row) { return row._sector === state.sector; });
                } else if (state.mode === 'product') {
                    filtered = rows.filter(function(row) { return row._product === state.product; });
                }
                return filtered.slice(0, 500);
            }

            function renderForwardChart(scope) {
                const node = document.getElementById('signal-forward-chart');
                const rows = scope.avgRows || [];
                if (!rows.length) {
                    if (window.Plotly) { try { Plotly.purge(node); } catch (err) {} }
                    node.innerHTML = emptyBox('暂无开仓信号样本');
                    return;
                }
                safePlot(node, [{
                    type: 'bar',
                    x: rows.map(r => r.horizon),
                    y: rows.map(r => r.avg_return_pct),
                    marker: {color: rows.map(r => (r.avg_return_pct || 0) >= 0 ? '#dc2626' : '#16a34a')},
                    text: rows.map(r => r.avg_return_pct === null || r.avg_return_pct === undefined ? '-' : r.avg_return_pct.toFixed(3) + '%'),
                    textposition: 'outside',
                    hovertemplate: '观察窗口: %{x}<br>方向收益均值: %{y:.3f}%<extra></extra>'
                }], {
                    height: 330,
                    margin: {l: 60, r: 24, t: 20, b: 50},
                    paper_bgcolor: 'rgba(0,0,0,0)',
                    plot_bgcolor: 'rgba(0,0,0,0)',
                    yaxis: {title: '方向收益均值(%)', zeroline: true, zerolinecolor: '#9ca3af', gridcolor: '#f3f4f6'},
                    xaxis: {title: '信号后观察窗口'},
                    showlegend: false
                }, {displayModeBar: false, responsive: true});
            }

            function renderDistribution(scope) {
                const node = document.getElementById('signal-distribution-chart');
                const dist = scope.distribution || {};
                if (!dist.x || !dist.x.length) {
                    if (window.Plotly) { try { Plotly.purge(node); } catch (err) {} }
                    node.innerHTML = emptyBox('暂无足够方向收益样本');
                    return;
                }
                safePlot(node, [{
                    type: 'bar',
                    x: dist.x,
                    y: dist.y,
                    width: dist.width,
                    marker: {color: '#64748b'},
                    opacity: 0.85,
                    hovertemplate: '收益区间中心: %{x:.3f}%<br>信号次数: %{y}<extra></extra>'
                }], {
                    height: 330,
                    margin: {l: 60, r: 24, t: 20, b: 50},
                    paper_bgcolor: 'rgba(0,0,0,0)',
                    plot_bgcolor: 'rgba(0,0,0,0)',
                    xaxis: {title: 'T+' + payload.primaryHorizon + ' Bar 方向收益(%)', zeroline: true, zerolinecolor: '#9ca3af'},
                    yaxis: {title: '信号次数', gridcolor: '#f3f4f6'},
                    showlegend: false
                }, {displayModeBar: false, responsive: true});
            }

            function renderIcChart(scope) {
                const node = document.getElementById('signal-ic-chart');
                const rows = scope.icRows || [];
                if (!rows.length) {
                    if (window.Plotly) { try { Plotly.purge(node); } catch (err) {} }
                    node.innerHTML = emptyBox('暂无足够 IC 样本');
                    return;
                }
                safePlot(node, [
                    {type: 'bar', x: rows.map(r => r['观察窗口']), y: rows.map(r => r.IC), name: 'IC', marker: {color: '#2563eb'}},
                    {type: 'bar', x: rows.map(r => r['观察窗口']), y: rows.map(r => r['Rank IC']), name: 'Rank IC', marker: {color: '#7c3aed'}}
                ], {
                    height: 330,
                    barmode: 'group',
                    margin: {l: 60, r: 24, t: 24, b: 50},
                    paper_bgcolor: 'rgba(0,0,0,0)',
                    plot_bgcolor: 'rgba(0,0,0,0)',
                    yaxis: {title: '相关系数', zeroline: true, zerolinecolor: '#9ca3af', gridcolor: '#f3f4f6'},
                    xaxis: {title: '信号后观察窗口'},
                    legend: {orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1}
                }, {displayModeBar: false, responsive: true});
            }

            function renderScope() {
                const scope = currentScope();
                document.getElementById('signal-scope-label').textContent = '当前范围：' + scope.label + '；信号事件 ' + scope.eventTotal + ' 条，开仓信号 ' + scope.entryTotal + ' 条。';
                renderCards(scope);
                document.getElementById('signal-ic-table').innerHTML = tableHtml(scope.icRows || []);
                document.getElementById('signal-group-title').textContent = scope.groupTitle || '分组统计';
                document.getElementById('signal-group-table').innerHTML = tableHtml(scope.groupRows || []);
                document.getElementById('signal-direction-table').innerHTML = tableHtml(scope.directionRows || []);
                document.getElementById('signal-month-table').innerHTML = tableHtml(scope.monthRows || []);
                document.getElementById('signal-reason-table').innerHTML = tableHtml(scope.reasonRows || []);
                document.getElementById('signal-event-table').innerHTML = tableHtml(filteredEventRows());
                document.getElementById('signal-event-note').textContent = '页面展示当前范围前 500 条；完整数据下载 CSV 后可在本地筛选。收益为信号方向收益，平仓信号不计算未来方向收益。';
                scheduleCharts(scope);
            }

            function updateModeButtons() {
                root.querySelectorAll('.signal-scope-mode').forEach(function(btn) {
                    const active = btn.dataset.mode === state.mode;
                    btn.className = 'signal-scope-mode px-4 py-2 text-sm font-medium ' + (active ? 'bg-[#1e3a8a] text-white' : 'bg-white text-gray-700 border-l border-gray-200');
                });
                document.getElementById('signal-sector-select').disabled = state.mode !== 'sector';
                document.getElementById('signal-product-select').disabled = state.mode !== 'product';
            }

            const sectorSelect = document.getElementById('signal-sector-select');
            const productSelect = document.getElementById('signal-product-select');
            setOptions(sectorSelect, payload.sectors || []);
            setOptions(productSelect, payload.products || []);
            sectorSelect.value = state.sector;
            productSelect.value = state.product;

            root.querySelectorAll('.signal-scope-mode').forEach(function(btn) {
                btn.addEventListener('click', function() {
                    state.mode = btn.dataset.mode || 'all';
                    updateModeButtons();
                    renderScope();
                });
            });
            sectorSelect.addEventListener('change', function() {
                state.sector = this.value;
                state.mode = 'sector';
                updateModeButtons();
                renderScope();
            });
            productSelect.addEventListener('change', function() {
                state.product = this.value;
                state.mode = 'product';
                updateModeButtons();
                renderScope();
            });

            updateModeButtons();
            renderScope();
            window.addEventListener('resize', function() {
                if (lastRenderedScope) scheduleCharts(lastRenderedScope);
            });
        })();
        </script>
        """
        return html.replace("__SIGNAL_PAYLOAD__", payload_json).replace("__CSV_NAME__", csv_name)

    def _get_signal_diagnostics_static_html_div(self):
        df = self._build_signal_diagnostics_df()
        if df.empty:
            stale_path = os.path.join(self.output_dir, "signal_events_full.csv")
            if os.path.exists(stale_path):
                os.remove(stale_path)
            return """
            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-8 text-center text-gray-500">
                \u672c\u6b21\u56de\u6d4b\u6ca1\u6709\u8bb0\u5f55\u5230\u53ef\u89c2\u6d4b\u4fe1\u53f7\u3002\u8bf7\u786e\u8ba4\u7b56\u7565\u7ee7\u627f GeneralSignalStrategy\uff0c\u5e76\u542f\u7528 record_signals=True\u3002            </div>
            """

        csv_name = "signal_events_full.csv"
        df.to_csv(os.path.join(self.output_dir, csv_name), index=False, encoding="utf-8-sig")

        entry_df = df[df["signal"].isin([1, -1])].copy()
        exit_count = int((df["signal"] == 0).sum())
        long_count = int((df["signal"] == 1).sum())
        short_count = int((df["signal"] == -1).sum())
        rebalance_summary = self._signal_rebalance_summary()
        horizons = self._signal_horizons()
        primary_horizon = 6 if 6 in horizons else horizons[min(2, len(horizons) - 1)]
        primary_col = f"fwd_{primary_horizon}_bar_return"
        primary_raw_col = f"fwd_{primary_horizon}_bar_raw_return"
        ic_df = self._build_signal_ic_df(entry_df, horizons)
        primary_ic_row = ic_df.loc[ic_df["horizon"] == f"T+{primary_horizon}"] if not ic_df.empty else pd.DataFrame()
        primary_ic = float(primary_ic_row["ic"].iloc[0]) if not primary_ic_row.empty else np.nan
        primary_rank_ic = float(primary_ic_row["rank_ic"].iloc[0]) if not primary_ic_row.empty else np.nan
        entry_df_for_ic = entry_df.copy()
        if not entry_df_for_ic.empty:
            entry_df_for_ic["月份"] = pd.to_datetime(entry_df_for_ic["datetime"]).dt.to_period("M").astype(str)
        monthly_ic_df = self._build_group_ic_df(entry_df_for_ic, "月份", primary_raw_col)
        valid_monthly_ic = monthly_ic_df["ic"].dropna() if "ic" in monthly_ic_df.columns else pd.Series(dtype=float)
        monthly_icir = (
            float(valid_monthly_ic.mean() / valid_monthly_ic.std(ddof=1))
            if valid_monthly_ic.size >= 2 and valid_monthly_ic.std(ddof=1) > 1e-12
            else np.nan
        )
        score_quantile_df = self._build_score_quantile_df(entry_df, primary_raw_col)

        if not entry_df.empty and primary_col in entry_df.columns:
            valid_primary = entry_df[primary_col].dropna()
            win_rate = float((valid_primary > 0).mean()) if not valid_primary.empty else np.nan
            avg_forward = float(valid_primary.mean()) if not valid_primary.empty else np.nan
        else:
            win_rate = np.nan
            avg_forward = np.nan

        avg_rows = []
        for horizon in horizons:
            col = f"fwd_{horizon}_bar_return"
            sample = entry_df[col].dropna() if col in entry_df.columns else pd.Series(dtype=float)
            avg_rows.append({
                "horizon": f"T+{horizon}",
                "avg_return": float(sample.mean() * 100) if not sample.empty else np.nan,
                "win_rate": float((sample > 0).mean() * 100) if not sample.empty else np.nan,
                "count": int(sample.count()),
            })
        avg_df = pd.DataFrame(avg_rows)

        positive_horizons = int((avg_df["avg_return"] > 0).sum()) if not avg_df.empty else 0
        balance_ratio = min(long_count, short_count) / max(long_count, short_count) if max(long_count, short_count) > 0 else np.nan
        avg_mfe = float(entry_df["fwd_mfe_24_bar"].mean()) if "fwd_mfe_24_bar" in entry_df and not entry_df.empty else np.nan
        avg_mae = float(entry_df["fwd_mae_24_bar"].mean()) if "fwd_mae_24_bar" in entry_df and not entry_df.empty else np.nan
        mfe_mae_ratio = avg_mfe / abs(avg_mae) if pd.notna(avg_mfe) and pd.notna(avg_mae) and abs(avg_mae) > 1e-12 else np.nan
        sample_note = "\u6837\u672c\u504f\u5c11" if len(entry_df) < 30 else ("\u6837\u672c\u4e2d\u7b49" if len(entry_df) < 100 else "\u6837\u672c\u8f83\u591a")

        cards = [
            ("\u4fe1\u53f7\u603b\u6570", f"{len(df):,}", "Recorded signal events"),
            ("\u5f00\u4ed3\u4fe1\u53f7", f"{len(entry_df):,}", f"Long {long_count:,} / Short {short_count:,}"),
            ("\u5e73\u4ed3\u4fe1\u53f7", f"{exit_count:,}", "Exit / Flat signals"),
            ("\u63d0\u4ea4\u8ba2\u5355", f"{rebalance_summary['submitted']:,}", "Orders submitted by rebalancer"),
            (f"T+{primary_horizon} Bar \u80dc\u7387", self._fmt_pct_value(win_rate), "Entry-signal directional hit rate"),
            (f"T+{primary_horizon} Bar \u5747\u503c", self._fmt_pct_value(avg_forward), "Entry-signal average return"),
            (f"T+{primary_horizon} IC", self._fmt_ic_value(primary_ic), "Signal score vs raw forward return"),
            (f"T+{primary_horizon} Rank IC", self._fmt_ic_value(primary_rank_ic), "Rank correlation"),
            ("\u6708\u5ea6 ICIR", self._fmt_number_value(monthly_icir, 2), "Mean monthly IC / std"),
            ("\u6837\u672c\u72b6\u6001", sample_note, "Entry signal sample size"),
            ("\u5468\u671f\u7a33\u5b9a\u6027", f"{positive_horizons}/{len(horizons)}", "Positive average horizons"),
            ("\u591a\u7a7a\u5747\u8861\u5ea6", self._fmt_pct_value(balance_ratio), "Min(long, short) / max(long, short)"),
            ("MFE/MAE ?", self._fmt_number_value(mfe_mae_ratio, 2), "Average favorable / adverse excursion"),
        ]
        cards_html = "".join(
            f"""
            <div class="border border-gray-100 rounded-lg p-4 bg-gray-50">
                <div class="text-xs text-gray-500">{subtitle}</div>
                <div class="text-sm font-semibold text-gray-700 mt-1">{title}</div>
                <div class="text-2xl font-bold text-gray-900 mt-2">{value}</div>
            </div>
            """
            for title, value, subtitle in cards
        )

        avg_rows = []
        for horizon in horizons:
            col = f"fwd_{horizon}_bar_return"
            sample = entry_df[col].dropna() if col in entry_df.columns else pd.Series(dtype=float)
            avg_rows.append({
                "horizon": f"T+{horizon}",
                "avg_return": float(sample.mean() * 100) if not sample.empty else np.nan,
                "win_rate": float((sample > 0).mean() * 100) if not sample.empty else np.nan,
                "count": int(sample.count()),
            })
        avg_df = pd.DataFrame(avg_rows)

        fig_avg = go.Figure()
        fig_avg.add_trace(go.Bar(
            x=avg_df["horizon"],
            y=avg_df["avg_return"],
            name="Average Forward Return",
            marker_color=np.where(avg_df["avg_return"] >= 0, "#dc2626", "#16a34a"),
            text=[f"{v:.3f}%" if pd.notna(v) else "-" for v in avg_df["avg_return"]],
            textposition="outside",
            hovertemplate="\u89c2\u5bdf\u7a97\u53e3: %{x} Bar<br>\u5f00\u4ed3\u65b9\u5411\u6536\u76ca\u5747\u503c: %{y:.3f}%<extra></extra>",
        ))
        fig_avg.update_layout(
            height=330,
            margin=dict(l=60, r=24, t=20, b=50),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(title="\u5f00\u4ed3\u65b9\u5411\u6536\u76ca\u5747\u503c(%)", zeroline=True, zerolinecolor="#9ca3af", gridcolor="#f3f4f6"),
            xaxis=dict(title="\u4fe1\u53f7\u540e\u89c2\u5bdf\u7a97\u53e3\uff0cT+N \u8868\u793a\u4fe1\u53f7\u540e\u7b2c N \u6839Bar"),
            showlegend=False,
        )
        html_avg_chart = fig_avg.to_html(full_html=False, include_plotlyjs=False)

        if not ic_df.empty:
            fig_ic = go.Figure()
            fig_ic.add_trace(go.Bar(
                x=ic_df["horizon"],
                y=ic_df["ic"],
                name="IC",
                marker_color="#2563eb",
                hovertemplate="观察窗口: %{x}<br>IC: %{y:.3f}<extra></extra>",
            ))
            fig_ic.add_trace(go.Bar(
                x=ic_df["horizon"],
                y=ic_df["rank_ic"],
                name="Rank IC",
                marker_color="#7c3aed",
                hovertemplate="观察窗口: %{x}<br>Rank IC: %{y:.3f}<extra></extra>",
            ))
            fig_ic.update_layout(
                height=330,
                barmode="group",
                margin=dict(l=60, r=24, t=24, b=50),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(title="相关系数", zeroline=True, zerolinecolor="#9ca3af", gridcolor="#f3f4f6"),
                xaxis=dict(title="\u4fe1\u53f7\u540e\u89c2\u5bdf\u7a97\u53e3"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            html_ic_chart = fig_ic.to_html(full_html=False, include_plotlyjs=False)

            ic_display = ic_df.copy()
            ic_display = ic_display.rename(columns={
                "horizon": "观察窗口",
                "ic": "IC",
                "rank_ic": "Rank IC",
                "count": "\u6837\u672c\u6570",
            })
            ic_display["IC"] = ic_display["IC"].map(self._fmt_ic_value)
            ic_display["Rank IC"] = ic_display["Rank IC"].map(self._fmt_ic_value)
            html_ic_table = self._compact_table_html(ic_display)
        else:
            html_ic_chart = "<div class=\'text-center text-gray-500 py-20\'>\u6682\u65e0\u8db3\u591f IC \u6837\u672c</div>"
            html_ic_table = "<div class=\'text-center text-gray-500 py-10\'>\u6682\u65e0\u8db3\u591f IC \u6837\u672c</div>"

        if not score_quantile_df.empty:
            quantile_display = score_quantile_df.copy()
            quantile_display["平均信号分数"] = quantile_display["平均信号分数"].map(lambda value: self._fmt_number_value(value, 4))
            quantile_display["\u5e73\u5747\u539f\u59cb\u6536\u76ca"] = quantile_display["\u5e73\u5747\u539f\u59cb\u6536\u76ca"].map(self._fmt_pct_value)
            quantile_display["胜率"] = quantile_display["胜率"].map(self._fmt_pct_value)
            html_quantile_table = self._compact_table_html(quantile_display)
        else:
            html_quantile_table = "<div class=\'text-center text-gray-500 py-10\'>\u4fe1\u53f7\u5206\u6570\u5c42\u7ea7\u4e0d\u8db3\uff0c\u6682\u4e0d\u751f\u6210\u5206\u5c42\u6536\u76ca</div>"

        valid_dist = entry_df[primary_col].dropna() * 100 if primary_col in entry_df.columns else pd.Series(dtype=float)
        if not valid_dist.empty:
            fig_dist = go.Figure()
            fig_dist.add_trace(go.Histogram(
                x=valid_dist,
                nbinsx=40,
                marker_color="#64748b",
                opacity=0.85,
                hovertemplate="Return bucket: %{x:.3f}%<br>Count: %{y}<extra></extra>",
            ))
            fig_dist.update_layout(
                height=330,
                margin=dict(l=60, r=24, t=20, b=50),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(title=f"T+{primary_horizon} Bar \u5f00\u4ed3\u65b9\u5411\u6536\u76ca(%)", zeroline=True, zerolinecolor="#9ca3af"),
                yaxis=dict(title="信号次数", gridcolor="#f3f4f6"),
                showlegend=False,
            )
            html_dist_chart = fig_dist.to_html(full_html=False, include_plotlyjs=False)
        else:
            html_dist_chart = "<div class=\'text-center text-gray-500 py-20\'>\u6682\u65e0\u8db3\u591f\u65b9\u5411\u6536\u76ca\u6837\u672c</div>"

        if not entry_df.empty:
            direction_perf = entry_df.copy()
            direction_perf["\u65b9\u5411"] = direction_perf["signal"].map({1: "\u591a\u5934\u4fe1\u53f7 (Long)", -1: "\u7a7a\u5934\u4fe1\u53f7 (Short)"})
            direction_perf = direction_perf.groupby("\u65b9\u5411").agg(
                signal_count=("signal", "size"),
                avg_return=(primary_col, "mean"),
                win_rate=(primary_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
                avg_mfe=("fwd_mfe_24_bar", "mean"),
                avg_mae=("fwd_mae_24_bar", "mean"),
            ).reset_index().rename(columns={
                "signal_count": "\u4fe1\u53f7\u6570",
                "avg_return": "\u5e73\u5747\u6536\u76ca",
                "win_rate": "\u80dc\u7387",
                "avg_mfe": "\u5e73\u5747MFE",
                "avg_mae": "\u5e73\u5747MAE",
            })
            for col in ["\u5e73\u5747\u6536\u76ca", "\u80dc\u7387", "\u5e73\u5747MFE", "\u5e73\u5747MAE"]:
                direction_perf[col] = direction_perf[col].map(self._fmt_pct_value)
            html_direction_table = self._compact_table_html(direction_perf)

            reason_perf = entry_df.copy()
            reason_perf["reason"] = reason_perf["reason"].fillna("").replace("", "unspecified")
            reason_perf["\u4fe1\u53f7\u65b9\u5411"] = reason_perf["signal"].map({1: "\u5f00\u591a/\u52a0\u591a", -1: "\u5f00\u7a7a/\u52a0\u7a7a"})
            reason_perf = reason_perf.groupby(["reason", "\u4fe1\u53f7\u65b9\u5411"]).agg(
                signal_count=("signal", "size"),
                avg_return=(primary_col, "mean"),
                win_rate=(primary_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
                avg_mfe=("fwd_mfe_24_bar", "mean"),
                avg_mae=("fwd_mae_24_bar", "mean"),
            ).reset_index().rename(columns={
                "reason": "\u539f\u56e0",
                "signal_count": "\u4fe1\u53f7\u6570",
                "avg_return": "\u5e73\u5747\u6536\u76ca",
                "win_rate": "\u80dc\u7387",
                "avg_mfe": "\u5e73\u5747MFE",
                "avg_mae": "\u5e73\u5747MAE",
            })
            for col in ["\u5e73\u5747\u6536\u76ca", "\u80dc\u7387", "\u5e73\u5747MFE", "\u5e73\u5747MAE"]:
                reason_perf[col] = reason_perf[col].map(self._fmt_pct_value)
            reason_perf = reason_perf.sort_values("\u4fe1\u53f7\u6570", ascending=False)
            html_reason_perf_table = self._compact_table_html(reason_perf)

            month_perf = entry_df.copy()
            month_perf["\u6708\u4efd"] = pd.to_datetime(month_perf["datetime"]).dt.to_period("M").astype(str)
            month_perf = month_perf.groupby("\u6708\u4efd").agg(
                signal_count=("signal", "size"),
                avg_return=(primary_col, "mean"),
                win_rate=(primary_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
            ).reset_index().rename(columns={
                "signal_count": "\u4fe1\u53f7\u6570",
                "avg_return": "\u5e73\u5747\u6536\u76ca",
                "win_rate": "\u80dc\u7387",
            })
            if not monthly_ic_df.empty:
                month_perf = month_perf.merge(monthly_ic_df.rename(columns={
                    "ic": "IC",
                    "rank_ic": "Rank IC",
                    "count": "IC\u6837\u672c\u6570",
                }), on="\u6708\u4efd", how="left")
            for col in ["\u5e73\u5747\u6536\u76ca", "\u80dc\u7387"]:
                month_perf[col] = month_perf[col].map(self._fmt_pct_value)
            for col in ["IC", "Rank IC"]:
                if col in month_perf.columns:
                    month_perf[col] = month_perf[col].map(self._fmt_ic_value)
            html_month_table = self._compact_table_html(month_perf.tail(24))
        else:
            html_direction_table = "<div class=\'text-center text-gray-500 py-10\'>\u6682\u65e0\u5f00\u4ed3\u65b9\u5411\u4fe1\u53f7</div>"
            html_reason_perf_table = "<div class=\'text-center text-gray-500 py-10\'>\u6682\u65e0\u5f00\u4ed3\u65b9\u5411\u4fe1\u53f7</div>"
            html_month_table = "<div class=\'text-center text-gray-500 py-10\'>\u6682\u65e0\u5f00\u4ed3\u65b9\u5411\u4fe1\u53f7</div>"

        if not entry_df.empty:
            symbol_stats = entry_df.groupby("symbol").agg(
                signal_count=("symbol", "size"),
                long_count=("signal", lambda s: int((s == 1).sum())),
                short_count=("signal", lambda s: int((s == -1).sum())),
                avg_return=(primary_col, "mean"),
                win_rate=(primary_col, lambda s: (s.dropna() > 0).mean() if s.dropna().size else np.nan),
                avg_mfe=("fwd_mfe_24_bar", "mean"),
                avg_mae=("fwd_mae_24_bar", "mean"),
            ).reset_index().rename(columns={
                "symbol": "Symbol",
                "signal_count": "Signal Count",
                "long_count": "Long Count",
                "short_count": "Short Count",
                "avg_return": "Average Return",
                "win_rate": "Hit Rate",
                "avg_mfe": "Average MFE",
                "avg_mae": "Average MAE",
            })
            symbol_stats["Average Return"] = symbol_stats["Average Return"].map(self._fmt_pct_value)
            symbol_stats["Hit Rate"] = symbol_stats["Hit Rate"].map(self._fmt_pct_value)
            symbol_stats["Average MFE"] = symbol_stats["Average MFE"].map(self._fmt_pct_value)
            symbol_stats["Average MAE"] = symbol_stats["Average MAE"].map(self._fmt_pct_value)
            symbol_stats = symbol_stats.sort_values("Signal Count", ascending=False)
            html_symbol_table = symbol_stats.to_html(
                index=False, border=0,
                classes="w-full text-xs text-center text-gray-700 bg-white"
            ).replace("<thead>", '<thead class="bg-gray-100 text-gray-700 sticky top-0">') \
             .replace("<th>", '<th class="py-2 px-3 text-center whitespace-nowrap">') \
             .replace("<td>", '<td class="py-2 px-3 text-center border-b border-gray-50">') \
             .replace('style="text-align: right;"', '')
        else:
            html_symbol_table = "<div class='text-center text-gray-500 py-10'>No entry signals</div>"

        reason_df = (
            df.fillna({"reason": ""})
            .assign(reason=lambda item: item["reason"].replace("", "unspecified"))
            .groupby(["reason", "signal"])
            .size()
            .reset_index(name="次数")
            .sort_values("次数", ascending=False)
            .head(50)
        )
        reason_df.columns = ["原因", "信号", "次数"]
        html_reason_table = reason_df.to_html(
            index=False, border=0,
            classes="w-full text-xs text-center text-gray-700 bg-white"
        ).replace("<thead>", '<thead class="bg-gray-100 text-gray-700 sticky top-0">') \
         .replace("<th>", '<th class="py-2 px-3 text-center whitespace-nowrap">') \
         .replace("<td>", '<td class="py-2 px-3 text-center border-b border-gray-50">') \
         .replace('style="text-align: right;"', '')

        preview_cols = [
            "datetime", "symbol", "signal", "reason", "current_net", "price",
            "signal_score", "size_scale", f"fwd_{horizons[0]}_bar_return",
            primary_col, primary_raw_col, "fwd_mfe_24_bar", "fwd_mae_24_bar",
        ]
        preview_cols = [col for col in preview_cols if col in df.columns]
        preview = df[preview_cols].head(500).copy()
        for col in [item for item in preview.columns if item.startswith("fwd_")]:
            preview[col] = preview[col].map(self._fmt_pct_value)
        if "datetime" in preview.columns:
            preview["datetime"] = pd.to_datetime(preview["datetime"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        preview = preview.rename(columns={
            "datetime": "时间",
            "symbol": "\u54c1\u79cd",
            "signal": "信号",
            "reason": "原因",
            "current_net": "\u5f53\u65f6\u51c0\u6301\u4ed3",
            "price": "\u4fe1\u53f7\u4ef7",
            "signal_score": "信号分数",
            "size_scale": "仓位系数",
            f"fwd_{horizons[0]}_bar_return": f"T+{horizons[0]} Bar收益",
            primary_col: f"T+{primary_horizon} Bar收益",
            primary_raw_col: f"T+{primary_horizon} Bar\u539f\u59cb\u6536\u76ca",
            "fwd_mfe_24_bar": "24 Bar MFE",
            "fwd_mae_24_bar": "24 Bar MAE",
        })
        html_preview_table = preview.to_html(
            index=False, border=0,
            classes="w-full text-xs text-center text-gray-700 bg-white"
        ).replace("<thead>", '<thead class="bg-gray-100 text-gray-700 sticky top-0">') \
         .replace("<th>", '<th class="py-2 px-3 text-center whitespace-nowrap">') \
         .replace("<td>", '<td class="py-2 px-3 text-center border-b border-gray-50">') \
         .replace('style="text-align: right;"', '')

        return f"""
        <div class="space-y-6">
            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                <div class="flex items-center justify-between mb-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">\u4fe1\u53f7\u68c0\u6d4b\u6982\u89c8 (Signal Inspection Overview)</h2>
                    <a href="{csv_name}" download class="bg-[#1e3a8a] hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium">下载信号明细 CSV</a>
                </div>
                <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">{cards_html}</div>
            </div>
            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3 mb-3">信号编码说明 (Signal Encoding)</h2>
                <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3 text-sm text-gray-700">
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = 1</b><br>\u5f00\u591a\u6216\u52a0\u591a\u5934\u3002\u662f\u5426\u771f\u7684\u6210\u4ea4\uff0c\u8fd8\u8981\u770b\u8c03\u4ed3\u5668\u3001\u4fdd\u8bc1\u91d1\u3001\u6302\u5355\u548c\u64ae\u5408\u3002</div>
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = -1</b><br>\u5f00\u7a7a\u6216\u52a0\u7a7a\u5934\u3002\u4f8b\uff1ashort_entry_high_zscore \u8868\u793a\u7b56\u7565\u7ed9\u51fa\u7684\u9ad8 zscore \u505a\u7a7a\u539f\u56e0\u3002</div>
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = 0</b><br>\u5e73\u4ed3\u6216\u51cf\u4ed3\u4fe1\u53f7\u3002\u534a\u5e73\u548c\u5168\u5e73\u90fd\u4f1a\u8868\u73b0\u4e3a 0\uff0c\u6240\u4ee5\u6570\u91cf\u5e38\u4f1a\u591a\u4e8e\u5f00\u4ed3\u4fe1\u53f7\u3002</div>
                    <div class="border border-gray-100 rounded-lg p-3 bg-gray-50"><b>signal = None</b><br>\u89c2\u671b\u6216\u65e0\u52a8\u4f5c\u4fe1\u53f7\u3002\u9ed8\u8ba4\u4e0d\u5c55\u793a\uff0c\u907f\u514d\u5927\u91cf hold / warming_up \u6df9\u6ca1\u6709\u6548\u4fe1\u53f7\u3002</div>
                </div>
            </div>
            <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                <h2 class="text-lg font-bold text-gray-800 border-l-4 border-indigo-600 pl-3 mb-2">IC \u68c0\u6d4b (Information Coefficient)</h2>
                <p class="text-xs text-gray-500 mb-3 pl-3">IC \u4f7f\u7528\u4fe1\u53f7\u5206\u6570\u4e0e\u672a\u6765\u539f\u59cb\u6536\u76ca\u8ba1\u7b97 Pearson \u76f8\u5173\uff1bRank IC \u4f7f\u7528 Spearman \u79e9\u76f8\u5173\u3002\u5b83\u8861\u91cf\u4fe1\u53f7\u5f3a\u5f31\u6392\u5e8f\u662f\u5426\u5bf9\u5e94\u672a\u6765\u6da8\u8dcc\uff0c\u4e0d\u7b49\u540c\u4e8e\u6700\u7ec8\u4ea4\u6613\u6536\u76ca\u3002</p>
                <div class="grid grid-cols-1 xl:grid-cols-3 gap-4">
                    <div class="xl:col-span-2">{html_ic_chart}</div>
                    <div class="overflow-y-auto max-h-[330px]">{html_ic_table}</div>
                </div>
                <div class="mt-4">
                    <h3 class="text-sm font-semibold text-gray-700 mb-2 pl-3">信号分数分层收益 (Score Quantile Return)</h3>
                    <div class="overflow-y-auto max-h-[280px]">{html_quantile_table}</div>
                </div>
            </div>
            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3 mb-2">\u5f00\u4ed3\u4fe1\u53f7\u540e\u65b9\u5411\u6536\u76ca (Entry Signal Forward Return)</h2>
                    <p class="text-xs text-gray-500 mb-2 pl-3">T+N \u8868\u793a\u4fe1\u53f7\u540e\u7b2c N \u6839Bar\uff0c\u4e0d\u4ee3\u8868\u6b63\u6536\u76ca\uff1b\u7ea2\u8272\u4e3a\u65b9\u5411\u6536\u76ca\u4e3a\u6b63\uff0c\u7eff\u8272\u4e3a\u65b9\u5411\u6536\u76ca\u4e3a\u8d1f\u3002\u8fd9\u91cc\u6309\u4fe1\u53f7\u5f53\u6839\u4ef7\u683c\u6d4b\u7b97\uff0c\u4e0d\u7b49\u540c\u4e8e\u4e0b\u4e00\u6839Bar\u7684\u771f\u5b9e\u6210\u4ea4\u6536\u76ca\u3002</p>
                    {html_avg_chart}
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 p-4">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-slate-600 pl-3 mb-2">\u5f00\u4ed3\u4fe1\u53f7\u540e\u6536\u76ca\u5206\u5e03 (Entry Signal Return Distribution)</h2>
                    <p class="text-xs text-gray-500 mb-2 pl-3">\u4ec5\u7edf\u8ba1 signal=1/-1 \u7684\u5f00\u4ed3\u65b9\u5411\u4fe1\u53f7\uff0csignal=0 \u7684\u5e73\u4ed3\u4fe1\u53f7\u4e0d\u53c2\u4e0e\u672a\u6765\u6536\u76ca\u5206\u5e03\u3002</p>
                    {html_dist_chart}
                </div>
            </div>
            <div class="grid grid-cols-1 xl:grid-cols-3 gap-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">多空方向表现 (By Direction)</h2>
                    </div>
                    <div class="overflow-y-auto max-h-[360px]">{html_direction_table}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">\u5f00\u4ed3\u539f\u56e0\u8868\u73b0 (Entry Reason Performance)</h2>
                    </div>
                    <div class="overflow-y-auto max-h-[360px]">{html_reason_perf_table}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">\u6708\u4efd\u7a33\u5b9a\u6027 (Monthly Stability)</h2>
                    </div>
                    <div class="overflow-y-auto max-h-[360px]">{html_month_table}</div>
                </div>
            </div>
            <div class="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">\u6309\u54c1\u79cd\u7edf\u8ba1 (By Symbol)</h2>
                    </div>
                    <div class="overflow-y-auto max-h-[420px]">{html_symbol_table}</div>
                </div>
                <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                    <div class="p-4 border-b border-gray-100">
                        <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">\u4fe1\u53f7\u539f\u56e0\u7edf\u8ba1 (By Reason)</h2>
                    </div>
                    <div class="overflow-y-auto max-h-[420px]">{html_reason_table}</div>
                </div>
            </div>
            <div class="bg-white rounded-xl shadow-md border border-gray-100 overflow-hidden">
                <div class="p-4 border-b border-gray-100 bg-cyan-50">
                    <h2 class="text-lg font-bold text-gray-800 border-l-4 border-cyan-600 pl-3">信号事件明细 (Signal Events)</h2>
                    <p class="text-xs text-gray-500 mt-2 pl-3">\u9875\u9762\u4ec5\u5c55\u793a\u524d 500 \u6761\uff0c\u5b8c\u6574\u6570\u636e\u8bf7\u4e0b\u8f7d CSV\u3002\u6536\u76ca\u4e3a\u4fe1\u53f7\u65b9\u5411\u6536\u76ca\uff1a\u591a\u5934\u770b\u4e0a\u6da8\uff0c\u7a7a\u5934\u770b\u4e0b\u8dcc\uff1b\u5e73\u4ed3\u4fe1\u53f7\u4e0d\u8ba1\u7b97\u672a\u6765\u65b9\u5411\u6536\u76ca\u3002</p>
                </div>
                <div class="overflow-y-auto max-h-[520px]">{html_preview_table}</div>
            </div>
        </div>
        """


    def get_metrics_table_html(self):
        """Build the compact performance metrics table."""
        if not hasattr(self, 'metrics_list') or not self.metrics_list:
            return "<div class=\'text-center text-gray-500 py-10\'>\u65e0\u7ee9\u6548\u6307\u6807</div>"

        df = pd.DataFrame(self.metrics_list)
        # 绩效表列数较多，使用紧凑字号和单元格间距保证首屏�??性�??
        html = df.to_html(index=False, border=0,
                          classes="w-full text-[11px] text-center text-gray-700 bg-white antialiased tracking-tighter")

        html = html.replace('<thead>', '<thead class="bg-[#2c3e50] text-white text-[11px] sticky top-0">') \
            .replace('<th>', '<th class="py-2 px-0.5 font-bold border-r border-[#34495e] whitespace-nowrap">') \
            .replace('<td>',
                     '<td class="py-1.5 px-0.5 border-b border-r border-gray-100 font-medium hover:bg-blue-50 transition-colors">')
        return html

    def get_params_table_html(self):
        """Build the backtest parameter table."""
        if not self.describe_params:
            return ""
        df = pd.DataFrame([self.describe_params])
        html = df.to_html(index=False, border=0,
                          classes="w-full text-sm text-center text-gray-600 bg-white shadow-sm rounded-lg overflow-hidden")
        # 移除 pandas 默�?右�?齐样式，保持参数表统�?居中�?
        html = html.replace('<thead>', '<thead class="bg-gray-100 text-gray-700 font-semibold border-b">') \
            .replace('<th>', '<th class="py-3 px-4 text-center">') \
            .replace('<td>', '<td class="py-3 px-4 text-center border-b border-gray-50">') \
            .replace('style="text-align: right;"', '')
        return html

    def _export_selection_records(self):
        if not self.selection_records:
            stale_path = os.path.join(self.output_dir, "selection_records_full.csv")
            if os.path.exists(stale_path):
                os.remove(stale_path)
            return
        df = pd.DataFrame(self.selection_records)
        output_path = os.path.join(self.output_dir, "selection_records_full.csv")
        df.to_csv(output_path, index=False, encoding="utf-8-sig")

    # =========================================================================
    # 报告生成入口
    # =========================================================================

    def generate_report(self):
        _safe_print("\n" + "=" * 50)
        _safe_print("[Analyzer] 正在生成绩效报告...")
        self._match_trades_fifo()
        self._calculate_metrics()
        self._export_selection_records()

        if not self.metrics:
            _safe_print("[Analyzer Warning] \u56de\u6d4b\u671f\u95f4\u65e0\u5b8c\u6574\u5f00\u5e73\u4ed3\u8bb0\u5f55\uff0c\u65e0\u6cd5\u751f\u6210\u5206\u6790\u62a5\u544a\u3002")
            return

        report_path = os.path.join(self.output_dir, '0_performance_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            header = f"=== {self.strategy_name} on {self.symbol} ({self.freq}) 绩效报告 ===\n"
            _safe_print(header)
            f.write(header)
            for k, v in self.metrics.items():
                line = f"{k}: {v}\n"
                _safe_print(f"  {k}: {v}")
                f.write(line)

        _safe_print("=" * 50)
        _safe_print("[Analyzer] \u6307\u6807\u8ba1\u7b97\u5b8c\u6210\uff0c\u6b63\u5728\u51c6\u5907\u524d\u7aef\u62a5\u544a\u6570\u636e\u3002")
