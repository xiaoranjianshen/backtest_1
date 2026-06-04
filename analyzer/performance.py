# -*- coding: utf-8 -*-
"""
工业级回测引擎 - 分析师模块 (Analyzer)
功能：FIFO 交易配对、量化指标计算，资金/价格对比图(单/多品种自适应)、交易 DNA 四宫格
"""
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from broker.order import Direction, Offset


def _lookup_multiplier(symbol: str) -> float:
    from config import FEE_DICT, pure_product_code
    raw_code = pure_product_code(symbol)
    meta = FEE_DICT.get(raw_code) or FEE_DICT.get(raw_code.upper()) or FEE_DICT.get(raw_code.lower())
    return meta['multiplier'] if meta else 10.0


def _lookup_meta(symbol: str) -> dict:
    from config import FEE_DICT, pure_product_code
    raw_code = pure_product_code(symbol)
    return FEE_DICT.get(raw_code) or FEE_DICT.get(raw_code.upper()) or FEE_DICT.get(raw_code.lower()) or {}


class StrategyAnalyzer:
    def __init__(self, trades: list, price_df: pd.DataFrame, initial_capital: float,
                 symbol: str, freq: str, strategy_name: str,
                 account_summary: dict = None, equity_df: pd.DataFrame = None,
                 describe_params: dict = None):
        self.trades = trades
        self.price_df = price_df.copy()
        self.initial_capital = initial_capital
        self.symbol = symbol.upper()
        self.freq = freq.lower()
        self.strategy_name = strategy_name
        self.account_summary = account_summary or {}
        self.equity_df = equity_df.copy() if equity_df is not None else None
        self.describe_params = describe_params or {}

        self.matched_trades = []
        self.metrics = {}
        self.metrics_list = []
        self.unmatched_close_volume = 0

        self.output_dir = os.path.join(PROJECT_ROOT, 'analyzer',
                                       f"{self.symbol}_{self.freq}_{self.strategy_name}_Backtest")
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def _match_trades_fifo(self):
        """FIFO 开平仓配对：按品种独立队列，换月流水不参与配对"""
        long_queues = defaultdict(list)
        short_queues = defaultdict(list)

        for t in self.trades:
            if getattr(t, 'is_rollover', False):
                continue

            sym = t.symbol
            is_close = t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]

            if not is_close:
                if t.direction == Direction.LONG:
                    long_queues[sym].append(t)
                else:
                    short_queues[sym].append(t)
                continue

            remain_vol = t.volume
            if t.direction == Direction.SHORT:
                queue = long_queues[sym]
                direction_label = 'Long'
            else:
                queue = short_queues[sym]
                direction_label = 'Short'

            while remain_vol > 0 and queue:
                target = queue[0]
                match_vol = min(remain_vol, target.volume)
                mult = _lookup_multiplier(sym)

                if direction_label == 'Long':
                    gross_pnl = (t.price - target.price) * match_vol * mult
                else:
                    gross_pnl = (target.price - t.price) * match_vol * mult

                open_comm = target.commission * (match_vol / (target.volume + 1e-9))
                close_comm = t.commission * (match_vol / (t.volume + 1e-9))
                net_pnl = gross_pnl - open_comm - close_comm

                self.matched_trades.append({
                    'symbol': sym,
                    'open_time': target.trade_time, 'open_price': target.price,
                    'close_time': t.trade_time, 'close_price': t.price,
                    'direction': direction_label, 'volume': match_vol,
                    'gross_pnl': gross_pnl, 'net_pnl': net_pnl, 'commission': open_comm + close_comm
                })

                target.volume -= match_vol
                remain_vol -= match_vol
                if target.volume <= 0:
                    queue.pop(0)

            self.unmatched_close_volume += remain_vol

        self.match_df = pd.DataFrame(self.matched_trades)
        if not self.match_df.empty:
            self.match_df['hold_time_hours'] = (
                self.match_df['close_time'] - self.match_df['open_time']
            ).dt.total_seconds() / 3600.0

    def _calculate_metrics(self):
        """计算绩效指标 (支持多品种按行展开)"""
        self.metrics_list = []

        # 1. 计算总指标
        total_metrics = self._calc_single_metrics(self.match_df, self.symbol, self.strategy_name, is_total=True)
        self.metrics_list.append(total_metrics)

        # 2. 计算各分品种明细
        if self.symbol == 'MULTI' and not self.match_df.empty:
            for sym, df_sym in self.match_df.groupby('symbol'):
                sym_metrics = self._calc_single_metrics(df_sym, sym, "-", is_total=False)
                # 💥 之前在这里强行覆盖 "-" 的老代码已被彻底抹除！
                self.metrics_list.append(sym_metrics)

        self.metrics = self.metrics_list[0]

    def _calc_single_metrics(self, df_match, sym_name, strat_name, is_total=False):
        """核心引擎：精准计算每一个维度，无死角"""
        total_trades = len(df_match) if not df_match.empty else 0
        total_net_pnl = df_match['net_pnl'].sum() if total_trades > 0 else 0.0
        total_commission = df_match['commission'].sum() if total_trades > 0 else 0.0

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
            daily_pnl = df_match_copy.groupby(df_match_copy['close_time'].dt.date)['net_pnl'].sum()
        else:
            win_rate_trade = pnl_ratio_trade = max_drawdown_trade = 0.0
            daily_pnl = pd.Series(dtype=float)

        if is_total and getattr(self, 'equity_df', None) is not None and not self.equity_df.empty:
            eq = self.equity_df.sort_values('datetime').drop_duplicates('datetime', keep='last')
            equity_curve = eq.set_index('datetime')['equity']
            daily_equity = equity_curve.resample('D').last().ffill().dropna()
            daily_returns = daily_equity.pct_change().dropna()
            final_equity = float(daily_equity.iloc[-1])
            max_open_value = float((eq['equity'] * 0.15).max()) if 'equity' in eq.columns else 0.0
        else:
            equity_curve = self.initial_capital + daily_pnl.cumsum()
            if equity_curve.empty:
                daily_returns = pd.Series(dtype=float)
                final_equity = self.initial_capital
            else:
                daily_returns = daily_pnl / self.initial_capital
                final_equity = float(equity_curve.iloc[-1])
            max_open_value = 0.0

        cum_net = final_equity - self.initial_capital
        total_return = cum_net / self.initial_capital

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

        annual_return = (total_net_pnl / self.initial_capital) * (365 / days)
        sharpe_ratio = (daily_returns.mean() / daily_returns.std() * np.sqrt(252)) if len(daily_returns) > 1 and daily_returns.std() > 0 else 0.0
        calmar_ratio = annual_return / abs(max_drawdown_rate) if max_drawdown_rate < 0 else float('inf')

        win_days = int((daily_pnl > 0).sum())
        total_days = len(daily_pnl)
        daily_win_rate = win_days / total_days if total_days > 0 else 0.0
        daily_pnl_pos = daily_pnl[daily_pnl > 0]
        daily_pnl_neg = daily_pnl[daily_pnl <= 0]
        daily_pnl_ratio = (daily_pnl_pos.mean() / abs(daily_pnl_neg.mean())) if len(daily_pnl_pos) > 0 and len(daily_pnl_neg) > 0 and daily_pnl_neg.mean() != 0 else 0.0
        avg_daily_volume = abs(daily_pnl).sum() / total_days if total_days > 0 else 0.0

        # 💥 底层换月拦截
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
            fee_rule = f"比例 {float(fee_open)*10000:.1f}‱"
        elif fee_type == 'fixed':
            fee_rule = f"固定 {float(fee_open)}元"
        else:
            fee_rule = "-"

        # 💥 最终字典：补回丢失的累计盈亏，确保 Pandas 不会产生 NaN！

        return {
            "合约": "总计 (MULTI)" if is_total else sym_name.upper(),
            "总收益": f"{total_return * 100:.2f}%",
            "年化收益": f"{annual_return * 100:.2f}%",
            "累计盈亏": f"¥{cum_net:,.0f}",  # 💥 统一使用统一的计算源，彻底解决分品种 NaN 问题
            "均笔利润(跳)": tick_profit,
            "最大开仓市值": f"¥{max_open_value:,.0f}" if is_total else "-",
            "单日最大回撤": f"¥{max_drawdown_trade:,.0f}",
            "最大回撤率": f"{max_drawdown_rate * 100:.2f}%",
            "年化Sharpe": f"{sharpe_ratio:.2f}",
            "卡玛比": f"{calmar_ratio:.2f}",
            "逐笔胜率": f"{win_rate_trade * 100:.2f}%",
            "逐笔盈亏比": f"{pnl_ratio_trade:.2f}",
            "逐日胜率": f"{daily_win_rate * 100:.2f}%",
            "逐日盈亏比": f"{daily_pnl_ratio:.2f}",
            "交易次数": total_trades,
            "交易日数": total_days,
            "日均成交额": f"¥{avg_daily_volume:,.0f}",
            "保证金": margin_rate_text,
            "主力换月次数": rollover_cnt_text,
            "换月手续费": rollover_fee_text,
            "费率模型": "-" if is_total else fee_rule,  # 💥 费率模型倒数第二列
            "累计手续费": f"¥{total_commission:,.0f}",  # 💥 累计手续费最后一列
        }

    # =========================================================================
    # 🌟 以下为全新架构：后端 HTML Div 生成器 (不再调用 fig.show() 和 .png)
    # =========================================================================

    def _get_equity_series(self):
        """获取用于画图的资金权益序列"""
        # 优先使用引擎记录的真实物理权益
        if getattr(self, 'equity_df', None) is not None and not self.equity_df.empty:
            df = self.equity_df.sort_values('datetime').drop_duplicates('datetime', keep='last')
            return df['datetime'].tolist(), df['equity'].tolist()

        # 兜底：如果没记录，就用平仓流水伪造一个
        if self.match_df.empty:
            return [], []
        df_sort = self.match_df.copy()
        df_sort['close_time'] = pd.to_datetime(df_sort['close_time'])
        daily_pnl = df_sort.sort_values('close_time').groupby(df_sort['close_time'].dt.date)['net_pnl'].sum()
        equity_curve = self.initial_capital + daily_pnl.cumsum()
        return list(equity_curve.index), equity_curve.tolist()

    def get_equity_html_div(self):
        """生成动态权益曲线的纯 HTML div"""
        if self.match_df.empty and (self.equity_df is None or self.equity_df.empty):
            return "<div class='text-center text-gray-500 py-10'>无资金权益数据</div>"

        equity_x, equity_y = self._get_equity_series()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=equity_x, y=equity_y, mode='lines', name='动态权益',
            line=dict(color='#3b82f6', width=2), fill='tozeroy', fillcolor='rgba(59,130,246,0.1)'
        ))
        fig.update_layout(
            height=400, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified", xaxis_title="", yaxis_title="权益 (￥)"
        )
        # 💥 核心：只吐出 div 字符串，不生成整个网页
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_cum_pnl_html_div(self):
        """生成累计盈亏与手续费的双 Y 轴 HTML div"""
        equity_x, equity_y = self._get_equity_series()
        if not equity_y:
            return "<div class='text-center text-gray-500 py-10'>无资金数据</div>"

        # 累计盈亏 = 动态权益 - 初始本金
        cum_pnl = [val - self.initial_capital for val in equity_y]

        # 累计交易手续费 (强制转换为 list，避免 Pandas 索引干扰)
        df_sort = self.match_df.copy()
        if not df_sort.empty:
            df_sort['close_time'] = pd.to_datetime(df_sort['close_time'])
            df_sort = df_sort.sort_values('close_time')
            comm_x = df_sort['close_time'].tolist()
            comm_y = df_sort['commission'].cumsum().tolist()
        else:
            comm_x = equity_x
            comm_y = [0] * len(equity_x)

        fig = go.Figure()

        # 💥 累计盈亏 (红线) - 悬浮保留精确数字
        fig.add_trace(go.Scatter(
            x=equity_x, y=cum_pnl,
            mode='lines', name='累计盈亏', line=dict(color='#ef4444', width=2),
            fill='tozeroy', fillcolor='rgba(239,68,68,0.1)',
            yaxis='y1',
            hovertemplate="日期: %{x}<br>累计盈亏: ¥%{y:,.0f}<extra></extra>"
        ))

        # 💥 累计手续费 (绿线) - 悬浮保留精确数字
        fig.add_trace(go.Scatter(
            x=comm_x, y=comm_y,
            mode='lines', name='累计交易手续费', line=dict(color='#22c55e', width=2),
            yaxis='y2',
            hovertemplate="日期: %{x}<br>累计手续费: ¥%{y:,.0f}<extra></extra>"
        ))

        fig.update_layout(
            height=350,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),

            # 💥 左侧主 Y 轴：删掉 tickformat，让 Plotly 自动使用 k / M 缩写
            yaxis=dict(
                title=dict(text="累计盈亏 (¥)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                showgrid=True,
                gridcolor='#f3f4f6'
            ),

            # 💥 右侧副 Y 轴：删掉 tickformat，让 Plotly 自动使用 k / M 缩写
            yaxis2=dict(
                title=dict(text="累计手续费 (¥)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                anchor="x",
                overlaying="y",
                side="right",
                showgrid=False
            )
        )

        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_net_value_with_benchmark_html_div(self):
        """生成净值曲线与基准对比 HTML div"""
        equity_x, equity_y = self._get_equity_series()
        if not equity_y:
            return "<div class='text-center text-gray-500 py-10'>无资金数据</div>"

        # 1. 计算策略绝对净值
        strategy_nv = [val / self.initial_capital for val in equity_y]

        # 2. 提取底层价格，计算等权基准净值 (Buy & Hold)
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
            # 💥 容错处理：确保每一个品种是从它自己的“上市第一天”作为 1.0 开始归一化，而不是全部按第一行
            norm_df = close_df.apply(lambda col: col / col.dropna().iloc[0] if not col.dropna().empty else col)
            # 等权均值：Pandas的mean会自动忽略NaN，实现动态等权买入持有！
            benchmark_nv = norm_df.mean(axis=1)

            # 对齐到动态权益的时间轴
            bench_series = pd.Series(benchmark_nv.values, index=pd.to_datetime(close_df.index))
            bench_series = bench_series[~bench_series.index.duplicated(keep='last')]
            idx = pd.to_datetime(equity_x)
            aligned_bench = bench_series.reindex(idx, method='ffill').bfill()
            bench_y = aligned_bench.tolist()
        else:
            bench_y = [1.0] * len(equity_x)

        fig = go.Figure()

        # 💥 策略净值 (蓝色面图)
        fig.add_trace(go.Scatter(
            x=equity_x, y=strategy_nv,
            mode='lines', name='策略净值', line=dict(color='#3b82f6', width=2),
            fill='tozeroy', fillcolor='rgba(59,130,246,0.1)',
            hovertemplate="日期: %{x}<br>策略净值: %{y:.4f}<extra></extra>"
        ))

        # 💥 基准净值 (橘色虚线)
        fig.add_trace(go.Scatter(
            x=equity_x, y=bench_y,
            mode='lines', name='等权买入持有基准', line=dict(color='#f59e0b', width=2, dash='dash'),
            hovertemplate="日期: %{x}<br>基准净值: %{y:.4f}<extra></extra>"
        ))

        fig.update_layout(
            height=350,
            margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(
                title=dict(text="净值", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                showgrid=True,
                gridcolor='#f3f4f6'
            )
        )

        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_rolling_drawdown_html_div(self):
        """生成滚动回撤 (Rolling Drawdown) 时序折线图"""
        if getattr(self, 'equity_df', None) is None or self.equity_df.empty:
            return "<div class='text-center text-gray-500 py-10'>无资金数据，无法计算滚动回撤</div>"

        df_eq = self.equity_df.sort_values('datetime').copy()

        # 1. 计算滚动最高点及回撤比例
        peak = df_eq['equity'].cummax()
        # 💥 强制转化为纯 list，彻底剥离 0-1500 的行号索引，防止 Plotly 误读！
        drawdown_y = ((df_eq['equity'] - peak) / peak).tolist()
        drawdown_x = df_eq['datetime'].tolist()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=drawdown_x, y=drawdown_y,
            mode='lines', name='动态回撤',
            line=dict(color='#dc2626', width=1.5),
            fill='tozeroy', fillcolor='rgba(220,38,38,0.08)',
            hovertemplate="日期: %{x}<br>回撤幅度: %{y:.2%}<extra></extra>"
        ))

        fig.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            yaxis=dict(
                title=dict(text="回撤幅度", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                tickformat=".2%",  # 恢复百分比格式
                showgrid=True,
                gridcolor='#f3f4f6'
            )
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_leverage_and_position_html_div(self):
        """还原每日历史仓位状态机，生成多空持仓名义本金与隔夜杠杆率双轴图"""
        if not self.trades or getattr(self, 'equity_df', None) is None or self.equity_df.empty:
            return "<div class='text-center text-gray-500 py-10'>持仓或权益数据不足，无法计算杠杆率</div>"

        # 1. 建立每日持仓时间流
        from collections import defaultdict
        df_eq = self.equity_df.sort_values('datetime').copy()
        df_eq['date'] = pd.to_datetime(df_eq['datetime']).dt.date
        date_list = sorted(df_eq['date'].unique())

        # 整理所有交易流水
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

        # 2. 状态机动态模拟每日持仓量
        daily_positions = {}
        current_pos = defaultdict(int)  # 符号代表方向：正为多，负为空

        # 建立价格字典加速查找
        price_records = []
        # 解析 price_df 宽表中的收盘价
        for dt, row in self.price_df.iterrows():
            d = dt.date()
            p_dict = {}
            for col in self.price_df.columns:
                if isinstance(col, tuple):
                    if col[0] in ['close', 'last_price']:
                        import re
                        m = re.search(r'\((.*?)\)', col[1])
                        if m: p_dict[m.group(1).lower()] = row[col]
                else:
                    p_dict[str(col).lower()] = row[col]
            price_records.append({'date': d, 'prices': p_dict})
        daily_prices = {r['date']: r['prices'] for r in price_records}

        daily_long_val = []
        daily_short_val = []
        daily_leverage = []

        # 线性步进回溯每一天
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
                            current_pos[sym] -= vol  # 平多仓位减少

            # 计算当天的名义市值
            p_map = daily_prices.get(d, {})
            long_v = 0.0
            short_v = 0.0

            for sym, vol in current_pos.items():
                if vol == 0: continue
                mult = _lookup_multiplier(sym)
                c_price = p_map.get(sym.lower(), 0.0)
                if c_price == 0.0: continue

                if vol > 0:
                    long_v += vol * c_price * mult
                else:
                    short_v += abs(vol) * c_price * mult

            daily_long_val.append(long_v)
            daily_short_val.append(-short_v)  # 空头名义本金转为负数用于条形图向下堆叠

            # 查找当天的权益
            day_equity = df_eq[df_eq['date'] == d]['equity'].iloc[-1]
            total_nominal = long_v + short_v
            daily_leverage.append(total_nominal / day_equity if day_equity > 0 else 0.0)

        # 3. 开启 Plotly 机构级双轴渲染
        fig = go.Figure()

        # 多头总敞口 (上方条形图)
        fig.add_trace(go.Bar(
            x=df_eq['datetime'], y=daily_long_val,
            name='多头总敞口', marker_color='rgba(239, 68, 68, 0.6)', yaxis='y1',
            hovertemplate="日期: %{x}<br>多头持仓名义价值: ¥%{y:,.0f}<extra></extra>"
        ))

        # 空头总敞口 (下方条形图)
        fig.add_trace(go.Bar(
            x=df_eq['datetime'], y=daily_short_val,
            name='空头总敞口', marker_color='rgba(34, 197, 94, 0.6)', yaxis='y1',
            hovertemplate="日期: %{x}<br>空头持仓名义价值: ¥%{y:,.0f}<extra></extra>"
        ))

        # 隔夜总杠杆率 (覆盖折线) - 绑定到副轴 y2
        fig.add_trace(go.Scatter(
            x=df_eq['datetime'], y=daily_leverage,
            mode='lines', name='隔夜总杠杆率', line=dict(color='#111827', width=2), yaxis='y2',
            hovertemplate="日期: %{x}<br>实际隔夜杠杆率: %{y:.2f} 倍<extra></extra>"
        ))

        fig.update_layout(
            height=380, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified", barmode='relative',
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),

            # 主轴：总敞口名义本金
            yaxis=dict(
                title=dict(text="名义持仓本金 (¥)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937")),

            # 副轴：实际杠杆率
            yaxis2=dict(
                title=dict(text="实际隔夜杠杆率 (倍)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                anchor="x", overlaying="y", side="right", showgrid=False,
                tickformat=".2f"
            )
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_multi_asset_pnl_bar_html_div(self):
        """生成多品种按盈亏从高到低排序的垂直直方图 (#11)"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty or self.symbol != 'MULTI':
            return "<div class='text-center text-gray-500 py-10'>非多品种组合或无交易数据，不生成品种盈亏直方图</div>"

        # 1. 按品种聚合盈亏并严格降序排列
        asset_stats = self.match_df.groupby('symbol')['net_pnl'].sum().sort_values(ascending=False)

        # 💥 强制转换为纯 Python 列表，彻底杜绝 Plotly 坐标轴错乱 Bug
        symbols = [str(sym).upper() for sym in asset_stats.index]
        pnls = [float(val) for val in asset_stats.values]

        # 盈利为红，亏损为绿 (国内期货标准)
        colors = ['#dc2626' if val > 0 else '#16a34a' for val in pnls]

        fig = go.Figure()

        # 💥 显式指定 x 和 y，确保是标准的垂直直方图
        fig.add_trace(go.Bar(
            x=symbols,
            y=pnls,
            marker_color=colors,
            text=[f"{val / 10000:.0f}万" for val in pnls],  # 柱子上直接显示万为单位的金额，一目了然
            textposition='auto',
            hovertemplate="合约: %{x}<br>累计净盈亏: ¥%{y:,.0f}<extra></extra>"
        ))

        fig.update_layout(
            height=350, margin=dict(l=10, r=10, t=20, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            showlegend=False,  # 💥 禁用多余的图例
            yaxis=dict(
                title=dict(text="累计净盈亏 (¥)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                showgrid=True, gridcolor='#f3f4f6'
            ),
            xaxis=dict(tickfont=dict(color="#1f2937", size=12, weight="bold"))
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_holding_period_pie_html_div(self):
        """生成独立的持仓周期占比饼图"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty:
            return "<div class='text-center text-gray-500 py-10'>无交易明细</div>"

        df_match = self.match_df.copy()

        df_match['close_time'] = pd.to_datetime(df_match['close_time'])
        df_match['open_time'] = pd.to_datetime(df_match['open_time'])
        df_match['hold_minutes'] = (df_match['close_time'] - df_match['open_time']).dt.total_seconds() / 60.0

        def _categorize_duration(mins):
            if mins <= 5.0:
                return "高频 (≤5分钟)"
            elif mins <= 24 * 60.0:
                return "日内 (5分-1天)"
            elif mins <= 5 * 24 * 60.0:
                return "短线 (1-5天)"
            elif mins <= 15 * 24 * 60.0:
                return "中线 (5-15天)"
            else:
                return "长线 (>15天)"

        df_match['duration_class'] = df_match['hold_minutes'].apply(_categorize_duration)
        order = ["高频 (≤5分钟)", "日内 (5分-1天)", "短线 (1-5天)", "中线 (5-15天)", "长线 (>15天)"]
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
            showlegend=False  # 💥 彻底干掉多余的 UI 按钮
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_turnover_pie_html_div(self):
        """生成独立的品种成交额占比饼图"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty:
            return "<div class='text-center text-gray-500 py-10'>无交易明细</div>"

        df_match = self.match_df.copy()
        df_match['turnover'] = df_match.apply(
            lambda r: r['volume'] * r['open_price'] * _lookup_multiplier(r['symbol']), axis=1
        )
        turnover_stats = df_match.groupby('symbol')['turnover'].sum()

        fig = go.Figure(data=[go.Pie(
            labels=[sym.upper() for sym in turnover_stats.index],
            values=turnover_stats.values.tolist(),
            textinfo='label+percent',
            hole=0.4
        )])

        fig.update_layout(
            height=300, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            showlegend=False  # 💥 彻底干掉多余的 UI 按钮
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_multi_asset_pnl_curves_html_div(self):
        """生成多品种盈亏曲线簇 (#12) - 独立时序累加"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty or self.symbol != 'MULTI':
            return "<div class='text-center text-gray-500 py-10'>无多品种交易流水</div>"

        df = self.match_df.copy()
        # 将平仓时间强制转换为标准化日期
        df['close_time'] = pd.to_datetime(df['close_time'])

        fig = go.Figure()

        # 提取资金曲线的时间轴作为公共坐标系基准，并标准化去重
        equity_x, _ = self._get_equity_series()
        base_idx = pd.to_datetime(equity_x).normalize().drop_duplicates() if equity_x else None

        for sym, grp in df.groupby('symbol'):
            # 严格按天聚合
            daily_pnl = grp.groupby(grp['close_time'].dt.normalize())['net_pnl'].sum()

            if base_idx is not None and not base_idx.empty:
                daily_pnl = daily_pnl.reindex(base_idx).fillna(0)

            cum_pnl = daily_pnl.cumsum()

            # 💥 强制转化为纯 List，彻底杜绝 Plotly 画行号的 Bug
            x_vals = cum_pnl.index.strftime('%Y-%m-%d').tolist()
            y_vals = cum_pnl.tolist()

            fig.add_trace(go.Scatter(
                x=x_vals, y=y_vals,
                mode='lines', name=sym.upper(),
                line=dict(width=1.5),
                hovertemplate=f"合约: {sym.upper()}<br>日期: %{{x}}<br>累计盈亏: ¥%{{y:,.0f}}<extra></extra>"
            ))

        fig.update_layout(
            height=400, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            yaxis=dict(
                title=dict(text="单品种累计盈亏 (¥)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"), showgrid=True, gridcolor='#f3f4f6'
            )
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_pnl_distribution_html_div(self):
        """生成逐笔极值盈亏分位数分布图 (#6) - 10%颗粒度，左右对称，全部向上"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty:
            return "<div class='text-center text-gray-500 py-10'>无交易明细</div>"

        df = self.match_df.copy()
        profits = df[df['net_pnl'] > 0]['net_pnl'].tolist()
        losses = df[df['net_pnl'] <= 0]['net_pnl'].tolist()

        # 设置 10% 到 100% 的颗粒阶梯 (步长为10)
        percentiles = list(range(10, 101, 10))

        # 1. 盈利单 (右侧，X为正，红色)
        if profits:
            p_quantiles = np.percentile(profits, percentiles).tolist()
            p_x = percentiles  # [10, 20, ..., 100]
        else:
            p_quantiles, p_x = [], []

        # 2. 亏损单 (左侧，X为负，绿色)
        # 注意：Y轴金额取绝对值(全部向上)，X轴取负数映射到左侧
        if losses:
            l_abs = np.abs(losses)
            l_quantiles = np.percentile(l_abs, percentiles).tolist()
            l_x = [-x for x in percentiles]  # [-10, -20, ..., -100]
        else:
            l_quantiles, l_x = [], []

        fig = go.Figure()

        # 渲染亏损单 (左侧区)
        if l_quantiles:
            fig.add_trace(go.Bar(
                x=l_x, y=l_quantiles, name='亏损绝对值分位数', marker_color='#16a34a',
                # 用 customdata 传回正数的百分位用于悬浮展示
                customdata=percentiles,
                hovertemplate="亏损极值: 第 %{customdata}% 分位<br>绝对亏损金额: ¥%{y:,.0f}<extra></extra>"
            ))

        # 渲染盈利单 (右侧区)
        if p_quantiles:
            fig.add_trace(go.Bar(
                x=p_x, y=p_quantiles, name='盈利绝对值分位数', marker_color='#dc2626',
                hovertemplate="盈利极值: 第 %{x}% 分位<br>绝对盈利金额: ¥%{y:,.0f}<extra></extra>"
            ))

        fig.update_layout(
            height=350, margin=dict(l=10, r=10, t=30, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            barmode='group',
            legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1),
            xaxis=dict(
                title=dict(text="盈亏极值分位数 (%)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                # 强制规范 X 轴刻度，展示为对称百分比
                tickvals=[-100, -80, -60, -40, -20, 0, 20, 40, 60, 80, 100],
                ticktext=["-100%", "-80%", "-60%", "-40%", "-20%", "0", "20%", "40%", "60%", "80%", "100%"],
                range=[-105, 105],  # 留出一点边距
                zeroline=True, zerolinecolor='#9ca3af', zerolinewidth=1.5  # 强化中心 0 轴
            ),
            yaxis=dict(
                title=dict(text="绝对极值金额 (¥)", font=dict(color="#1f2937")),
                tickfont=dict(color="#1f2937"),
                showgrid=True, gridcolor='#f3f4f6'
            )
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_period_returns_html_div(self):
        """生成多周期收益日历条形图 (#7) - 带 UI 切换按钮，已修复重叠与周线空白 Bug"""
        if getattr(self, 'equity_df', None) is None or self.equity_df.empty:
            return "<div class='text-center text-gray-500 py-10'>无资金数据</div>"

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

        # 💥 修复周线空白Bug: 将格式改为标准的 %Y-%m-%d，确保 Plotly 能正确渲染 X 轴
        w_labels, w_rets, w_colors = _calc_returns('W-FRI', '%Y-%m-%d')
        m_labels, m_rets, m_colors = _calc_returns('ME', '%Y-%m')
        y_labels, y_rets, y_colors = _calc_returns('YE', '%Y')

        fig = go.Figure()
        fig.add_trace(go.Bar(x=w_labels, y=w_rets, marker_color=w_colors, visible=False, name='周收益',
                             hovertemplate="截止周五: %{x}<br>收益率: %{y:.2%}<extra></extra>"))
        fig.add_trace(go.Bar(x=m_labels, y=m_rets, marker_color=m_colors, visible=True, name='月收益',
                             hovertemplate="月份: %{x}<br>收益率: %{y:.2%}<extra></extra>"))
        fig.add_trace(go.Bar(x=y_labels, y=y_rets, marker_color=y_colors, visible=False, name='年收益',
                             hovertemplate="年份: %{x}<br>收益率: %{y:.2%}<extra></extra>"))

        # 💥 将 UI 按钮强行移至左上角，彻底避开右上角的 Modebar 遮挡！
        fig.update_layout(
            updatemenus=[dict(
                type="buttons", direction="right", active=1,  # 💥 修复：Plotly 横向排列必须写 "right"
                x=0.0, y=1.15, xanchor="left", yanchor="top",  # 坐标定在左上
                buttons=list([
                    dict(label="周线", method="update", args=[{"visible": [True, False, False]}]),
                    dict(label="月线", method="update", args=[{"visible": [False, True, False]}]),
                    dict(label="年线", method="update", args=[{"visible": [False, False, True]}])
                ]),
                pad={"r": 10, "t": 10}, showactive=True, bgcolor="#f3f4f6", bordercolor="#d1d5db"
            )],
            height=350, margin=dict(l=10, r=10, t=50, b=10),  # 增加顶部边距给按钮留出空间
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', hovermode="x unified",
            yaxis=dict(title=dict(text="区间收益率", font=dict(color="#1f2937")), tickformat=".1%", showgrid=True,
                       gridcolor='#f3f4f6'),
            xaxis=dict(tickfont=dict(size=10), tickangle=-45)
        )
        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_replay_charts_dict(self):
        """生成单品种价格曲线与买卖点复盘图的 HTML Div 字典 (#8)"""
        if not self.trades or getattr(self, 'price_df', None) is None or self.price_df.empty:
            return {}

        replay_dicts = {}
        # 提取所有交易过的品种，去重并排序
        traded_symbols = sorted(list(set([t.symbol.lower() for t in self.trades])))

        # 💥 彻底剥离 Pandas 索引，强制转换为 Python 标准日期列表
        if 'datetime' in self.price_df.columns:
            dates = pd.to_datetime(self.price_df['datetime']).tolist()
        else:
            dates = pd.to_datetime(self.price_df.index).tolist()

        for sym in traded_symbols:
            # 模糊匹配找到价格宽表里对应的列
            sym_col = None
            import re
            for col in self.price_df.columns:
                if col == 'datetime': continue
                col_str = str(col[1]).lower() if isinstance(col, tuple) else str(col).lower()
                # 尝试精确匹配品种后缀，避免 'y' 匹配到其他字符
                match = re.search(r'\.([a-z]+)$', col_str)
                code = match.group(1) if match else col_str
                if sym == code or sym in col_str:
                    sym_col = col
                    break

            if sym_col is None:
                continue

            # 💥 最核心修复：将价格列强制转为 float，剔除 pd.NA 造成的 Object 类别干扰，防止 Plotly 画成行号序列！
            prices = pd.to_numeric(self.price_df[sym_col], errors='coerce').tolist()

            fig = go.Figure()

            # 1. 绘制底层价格主线 (沉稳的灰色极简风)
            fig.add_trace(go.Scatter(
                x=dates, y=prices, mode='lines', name='收盘价',
                line=dict(color='#9ca3af', width=1.5),
                connectgaps=True,  # 忽略缺失值断点
                hovertemplate="时间: %{x}<br>收盘价: ¥%{y:,.0f}<extra></extra>"
            ))

            # 2. 提取当前品种的开平仓坐标
            sym_trades = [t for t in self.trades if t.symbol.lower() == sym]

            ol_x, ol_y, ol_text = [], [], []  # 开多
            cl_x, cl_y, cl_text = [], [], []  # 平多
            os_x, os_y, os_text = [], [], []  # 开空
            cs_x, cs_y, cs_text = [], [], []  # 平空

            for t in sym_trades:
                if getattr(t, 'is_rollover', False): continue

                txt = f"手数: {t.volume}<br>成交价: ¥{t.price:,.0f}"

                # 💥 核心修复：Direction 代表订单买卖方向！

                # 1. 买入开仓 (Buy Open) = 开多
                if t.direction == Direction.LONG and t.offset == Offset.OPEN:
                    ol_x.append(t.trade_time);
                    ol_y.append(float(t.price));
                    ol_text.append(txt)

                # 2. 卖出平仓 (Sell Close) = 平多
                elif t.direction == Direction.SHORT and t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]:
                    cl_x.append(t.trade_time);
                    cl_y.append(float(t.price));
                    cl_text.append(txt)

                # 3. 卖出开仓 (Sell Open) = 开空
                elif t.direction == Direction.SHORT and t.offset == Offset.OPEN:
                    os_x.append(t.trade_time);
                    os_y.append(float(t.price));
                    os_text.append(txt)

                # 4. 买入平仓 (Buy Close) = 平空
                elif t.direction == Direction.LONG and t.offset in [Offset.CLOSE, Offset.CLOSE_TODAY]:
                    cs_x.append(t.trade_time);
                    cs_y.append(float(t.price));
                    cs_text.append(txt)
            # 3. 绘制买卖点图层 (极其符合国内投研直觉的图例)
            if ol_x:
                # 开多 (买入开仓) -> 红色实心正三角
                fig.add_trace(go.Scatter(x=ol_x, y=ol_y, mode='markers', name='开多 (Open Long)',
                                         marker=dict(symbol='triangle-up', size=13, color='#dc2626',
                                                     line=dict(width=1, color='white')),
                                         text=ol_text,
                                         hovertemplate="<b>【开多】</b><br>时间: %{x}<br>%{text}<extra></extra>"))
            if cl_x:
                # 平多 (卖出平仓) -> 绿色空心正方形
                fig.add_trace(go.Scatter(x=cl_x, y=cl_y, mode='markers', name='平多 (Close Long)',
                                         marker=dict(symbol='square-open', size=11, color='#16a34a',
                                                     line=dict(width=2.5)),
                                         text=cl_text,
                                         hovertemplate="<b>【平多】</b><br>时间: %{x}<br>%{text}<extra></extra>"))
            if os_x:
                # 开空 (卖出开仓) -> 绿色实心倒三角
                fig.add_trace(go.Scatter(x=os_x, y=os_y, mode='markers', name='开空 (Open Short)',
                                         marker=dict(symbol='triangle-down', size=13, color='#16a34a',
                                                     line=dict(width=1, color='white')),
                                         text=os_text,
                                         hovertemplate="<b>【开空】</b><br>时间: %{x}<br>%{text}<extra></extra>"))
            if cs_x:
                # 平空 (买入平仓) -> 红色空心正方形
                fig.add_trace(go.Scatter(x=cs_x, y=cs_y, mode='markers', name='平空 (Close Short)',
                                         marker=dict(symbol='square-open', size=11, color='#dc2626',
                                                     line=dict(width=2.5)),
                                         text=cs_text,
                                         hovertemplate="<b>【平空】</b><br>时间: %{x}<br>%{text}<extra></extra>"))

            fig.update_layout(
                height=500, margin=dict(l=10, r=10, t=10, b=10),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', hovermode="closest",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                yaxis=dict(title=dict(text="标的价格", font=dict(color="#1f2937")), tickfont=dict(color="#1f2937"),
                           showgrid=True, gridcolor='#f3f4f6'),
                xaxis=dict(showgrid=False, tickfont=dict(color="#1f2937"))
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
        df_eq['date'] = df_eq['datetime'].dt.date

        # 提取每日发生的手续费序列
        if hasattr(self, 'match_df') and not self.match_df.empty:
            df_match = self.match_df.copy()
            df_match['date'] = pd.to_datetime(df_match['close_time']).dt.date
            daily_comm = df_match.groupby('date')['commission'].sum()
        else:
            daily_comm = pd.Series(dtype=float)

        # 合并手续费并计算累计值
        df_eq = df_eq.merge(daily_comm.rename('daily_comm'), on='date', how='left').fillna({'daily_comm': 0})
        df_eq['累计手续费'] = df_eq['daily_comm'].cumsum()

        # 组装最终呈现的表单格式
        df_res = pd.DataFrame({
            '时间': df_eq['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S'),
            '动态权益': df_eq['equity'].round(2),
            '累计盈亏': (df_eq['equity'] - self.initial_capital).round(2),
            '累计手续费': df_eq['累计手续费'].round(2)
        })
        return df_res


    def get_metrics_table_html(self):
        """利用 Pandas 直接生成高度紧凑、自适应全屏的专业多列矩阵"""
        if not hasattr(self, 'metrics_list') or not self.metrics_list:
            return "<div class='text-center text-gray-500 py-10'>无绩效计算结果</div>"

        df = pd.DataFrame(self.metrics_list)
        # 💥 将字号统一缩减为 text-[11px]（紧凑抗锯齿体），紧凑排布 tracking-tighter
        html = df.to_html(index=False, border=0,
                          classes="w-full text-[11px] text-center text-gray-700 bg-white antialiased tracking-tighter")

        # 💥 极致压缩单元格边距（py-1.5 px-0.5），使 22 列不需要任何滑轮滚动就能在横向完全平铺
        html = html.replace('<thead>', '<thead class="bg-[#2c3e50] text-white text-[11px] sticky top-0">') \
            .replace('<th>', '<th class="py-2 px-0.5 font-bold border-r border-[#34495e] whitespace-nowrap">') \
            .replace('<td>',
                     '<td class="py-1.5 px-0.5 border-b border-r border-gray-100 font-medium hover:bg-blue-50 transition-colors">')
        return html

    def get_params_table_html(self):
        """生成基础参数表 HTML"""
        if not self.describe_params:
            return ""
        df = pd.DataFrame([self.describe_params])
        html = df.to_html(index=False, border=0,
                          classes="w-full text-sm text-center text-gray-600 bg-white shadow-sm rounded-lg overflow-hidden")
        # 💥 精准微调：替换增加 text-center，并用空字符串抹除 style="text-align: right;"
        html = html.replace('<thead>', '<thead class="bg-gray-100 text-gray-700 font-semibold border-b">') \
            .replace('<th>', '<th class="py-3 px-4 text-center">') \
            .replace('<td>', '<td class="py-3 px-4 text-center border-b border-gray-50">') \
            .replace('style="text-align: right;"', '')
        return html

    # =========================================================================
    # 报告入口改造
    # =========================================================================

    def generate_report(self):
        print("\n" + "=" * 50)
        print("生成报告")
        self._match_trades_fifo()
        self._calculate_metrics()

        if not self.metrics:
            print("⚠️ 警告：回测期间无完整开平仓记录，无法生成分析报告。")
            return

        report_path = os.path.join(self.output_dir, '0_performance_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            header = f"=== {self.strategy_name} on {self.symbol} ({self.freq}) 绩效报告 ===\n"
            print(header)
            f.write(header)
            for k, v in self.metrics.items():
                line = f"{k}: {v}\n"
                print(f"  {k}: {v}")
                f.write(line)

        print("=" * 50)
        print("✅ [后端就绪] 指标计算完毕。纯净数据已备好，准备进入前端渲染流程！")
        # 💥 绝对不要再调用 self._plot_equity_series()，我们不再画死板的图片了！
