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

        total_metrics = self._calc_single_metrics(self.match_df, self.symbol, self.strategy_name, is_total=True)

        rollover_cnt = self.account_summary.get('rollover_count')
        if rollover_cnt is not None:
            total_metrics["主力换月次数"] = rollover_cnt
        rollover_fee = self.account_summary.get('rollover_commission')
        if rollover_fee is not None and rollover_fee > 0:
            total_metrics["换月手续费"] = f"¥{rollover_fee:,.0f}"
        else:
            total_metrics["换月手续费"] = "-"

        self.metrics_list.append(total_metrics)

        if self.symbol == 'MULTI' and not self.match_df.empty:
            for sym, df_sym in self.match_df.groupby('symbol'):
                sym_metrics = self._calc_single_metrics(df_sym, sym, "-", is_total=False)
                sym_metrics["主力换月次数"] = "-"
                sym_metrics["换月手续费"] = "-"
                self.metrics_list.append(sym_metrics)

        self.metrics = self.metrics_list[0]
        self.metrics["累计盈亏"] = self.metrics["累计净值"]

    def _calc_single_metrics(self, df_match, sym_name, strat_name, is_total=False):
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

            # 💥 修复：兼容 datetime 是普通列还是索引 (Index) 的情况
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

        rollover_cnt = self.account_summary.get('rollover_count', '-')
        rollover_fee = self.account_summary.get('rollover_commission', 0)
        rollover_fee_text = f"¥{rollover_fee:,.0f}" if rollover_fee and rollover_fee > 0 else '-'

        return {
            "合约": sym_name.upper(),
            "参数": strat_name,
            "初始资金": f"¥{self.initial_capital:,.0f}" if is_total else "-",
            "总收益": f"{total_return * 100:.2f}%",
            "累计手续费": f"¥{total_commission:,.0f}",
            "累计净值": f"¥{cum_net:,.0f}",
            "累计盈亏": f"¥{cum_net:,.0f}",
            "年化收益": f"{annual_return * 100:.2f}%",
            "单笔利润跳数": f"{total_net_pnl / (tick_size * multiplier):.0f}" if tick_size > 0 and multiplier > 0 else "-",
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
            "主力换月次数": rollover_cnt,
            "换月手续费": rollover_fee_text,
        }

    # =========================================================================
    # 🌟 以下为全新架构：后端 HTML Div 生成器 (不再调用 fig.show() 和 .png)
    # =========================================================================

    def _get_equity_series(self):
        """获取用于画图的资金权益序列"""
        # 优先使用引擎盯市记录的真实物理权益
        if getattr(self, 'equity_df', None) is not None and not self.equity_df.empty:
            df = self.equity_df.sort_values('datetime').drop_duplicates('datetime', keep='last')
            return df['datetime'].tolist(), df['equity'].tolist()

        # 兜底：如果没记录盯市，就用平仓流水伪造一个
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
        """生成累计盈亏与手续费的纯 HTML div"""
        if getattr(self, 'match_df', None) is None or self.match_df.empty:
            return "<div class='text-center text-gray-500 py-10'>无交易明细数据</div>"

        df_sort = self.match_df.copy()
        df_sort['close_time'] = pd.to_datetime(df_sort['close_time'])

        # 💥 必须按日期 groupby 聚合，否则同一天有多笔交易 Plotly 会直接崩溃！
        daily_stats = df_sort.groupby(df_sort['close_time'].dt.date).agg({
            'net_pnl': 'sum',
            'commission': 'sum'
        }).sort_index()

        daily_stats['cum_pnl'] = daily_stats['net_pnl'].cumsum()
        daily_stats['cum_comm'] = daily_stats['commission'].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=daily_stats.index, y=daily_stats['cum_pnl'],
            mode='lines', name='累计盈亏', line=dict(color='#ef4444', width=2),
            fill='tozeroy', fillcolor='rgba(239,68,68,0.1)'
        ))
        fig.add_trace(go.Scatter(
            x=daily_stats.index, y=daily_stats['cum_comm'],
            mode='lines', name='累计手续费', line=dict(color='#22c55e', width=2)
        ))

        fig.update_layout(
            height=350, margin=dict(l=10, r=10, t=10, b=10),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        # 💥 强制添加 Y 轴单位(元)，并添加逗号分隔符
        fig.update_yaxes(title_text="金额 (¥)", tickformat=",")

        return fig.to_html(full_html=False, include_plotlyjs=False)

    def get_metrics_table_html(self):
        """利用 Pandas 直接生成 Tailwind 风格的多行绩效表格"""
        if not hasattr(self, 'metrics_list') or not self.metrics_list:
            return "<div class='text-center text-gray-500 py-10'>无绩效计算结果</div>"

        df = pd.DataFrame(self.metrics_list)
        # 用 pandas to_html 生成原生 table，配合 Tailwind 类名
        html = df.to_html(index=False, border=0, classes="w-full text-sm text-center text-gray-700 bg-white")
        # 替换表头和单元格样式
        html = html.replace('<thead>', '<thead class="bg-[#2c3e50] text-white text-xs sticky top-0">') \
            .replace('<th>', '<th class="py-3 px-2 whitespace-nowrap border-r border-[#34495e]">') \
            .replace('<td>', '<td class="py-2 px-2 border-b border-r border-gray-100 hover:bg-blue-50">')
        return html

    def get_params_table_html(self):
        """生成基础参数表 HTML"""
        if not self.describe_params:
            return ""
        df = pd.DataFrame([self.describe_params])
        html = df.to_html(index=False, border=0,
                          classes="w-full text-sm text-center text-gray-600 bg-white shadow-sm rounded-lg overflow-hidden")
        html = html.replace('<thead>', '<thead class="bg-gray-100 text-gray-700 font-semibold border-b">') \
            .replace('<th>', '<th class="py-3 px-4">') \
            .replace('<td>', '<td class="py-3 px-4 border-b border-gray-50">')
        return html

    # =========================================================================
    # 报告入口改造
    # =========================================================================
    def generate_report(self):
        print("\n" + "=" * 50)
        print("执行绩效计算模块...")
        self._match_trades_fifo()
        self._calculate_metrics()

        if not self.metrics:
            print("⚠️ 警告：回测期间无完整开平仓记录，无数据。")
            return

        report_path = os.path.join(self.output_dir, '0_performance_report.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            header = f"=== {self.strategy_name} on {self.symbol} ({self.freq}) 核心指标 ===\n"
            f.write(header)
            for k, v in self.metrics.items():
                f.write(f"{k}: {v}\n")

        print("=" * 50)
        print(f"✅ [后端就绪] 指标计算完毕。准备生成前端交互看板...")
        # 注意：这里彻底移除了 self._plot_equity_series() 的调用！

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
