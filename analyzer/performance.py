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


class StrategyAnalyzer:
    def __init__(self, trades: list, price_df: pd.DataFrame, initial_capital: float,
                 symbol: str, freq: str, strategy_name: str,
                 account_summary: dict = None, equity_df: pd.DataFrame = None):
        self.trades = trades
        self.price_df = price_df.copy()
        self.initial_capital = initial_capital
        self.symbol = symbol.upper()
        self.freq = freq.lower()
        self.strategy_name = strategy_name
        self.account_summary = account_summary or {}
        self.equity_df = equity_df.copy() if equity_df is not None else None

        self.matched_trades = []
        self.metrics = {}
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
                continue  # 换月流水不参与策略开平配对

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
        """计算绩效指标：优先用引擎盯市权益曲线，配对盈亏与账户交叉验证"""
        if self.match_df.empty and (self.equity_df is None or self.equity_df.empty):
            return

        total_trades = len(self.match_df) if not self.match_df.empty else 0
        win_rate = 0.0
        pnl_ratio = 0.0
        avg_hold = 0.0
        total_net_pnl = 0.0
        total_commission = 0.0

        if not self.match_df.empty:
            win_trades = len(self.match_df[self.match_df['net_pnl'] > 0])
            win_rate = win_trades / total_trades if total_trades > 0 else 0
            total_net_pnl = self.match_df['net_pnl'].sum()
            total_commission = self.match_df['commission'].sum()
            avg_win = self.match_df[self.match_df['net_pnl'] > 0]['net_pnl'].mean()
            avg_loss = abs(self.match_df[self.match_df['net_pnl'] <= 0]['net_pnl'].mean()) if len(self.match_df[self.match_df['net_pnl'] <= 0]) > 0 else 0.0
            pnl_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
            avg_hold = self.match_df['hold_time_hours'].mean()

        if self.equity_df is not None and not self.equity_df.empty:
            eq = self.equity_df.sort_values('datetime').drop_duplicates('datetime', keep='last')
            equity_curve = eq.set_index('datetime')['equity']
            daily_equity = equity_curve.resample('D').last().ffill().dropna()
            daily_returns = daily_equity.pct_change().dropna()
            final_equity = float(daily_equity.iloc[-1])
            total_return = final_equity / self.initial_capital - 1.0
            peak = daily_equity.cummax()
            drawdown = (daily_equity - peak) / peak
            max_drawdown = float(drawdown.min()) if len(drawdown) else 0.0
            days = max((daily_equity.index[-1] - daily_equity.index[0]).days, 1)
            annual_return = (1 + total_return) ** (365 / days) - 1
            sharpe_ratio = (
                daily_returns.mean() / daily_returns.std() * np.sqrt(252)
                if len(daily_returns) > 1 and daily_returns.std() > 0 else 0.0
            )
            calmar_ratio = annual_return / abs(max_drawdown) if max_drawdown < 0 else float('inf')
        else:
            daily_pnl = self.match_df.groupby(self.match_df['close_time'].dt.date)['net_pnl'].sum()
            if daily_pnl.empty:
                return
            cum_pnl = daily_pnl.cumsum()
            equity_curve = self.initial_capital + cum_pnl
            peak = equity_curve.cummax()
            drawdown = (equity_curve - peak) / peak
            max_drawdown = drawdown.min()
            days = (self.price_df['datetime'].max() - self.price_df['datetime'].min()).days
            days = max(days, 1)
            annual_return = (total_net_pnl / self.initial_capital) * (365 / days)
            daily_returns = daily_pnl / self.initial_capital
            sharpe_ratio = (
                daily_returns.mean() / daily_returns.std() * np.sqrt(252)
                if daily_returns.std() > 0 else 0.0
            )
            calmar_ratio = annual_return / abs(max_drawdown) if max_drawdown < 0 else float('inf')
            final_equity = float(equity_curve.iloc[-1])

        self.metrics = {
            "初始资金": f"￥{self.initial_capital:,.2f}",
            "最终动态权益": f"￥{final_equity:,.2f}",
            "配对净利润(扣费)": f"￥{total_net_pnl:,.2f}",
            "总手续费摩擦": f"￥{total_commission:,.2f}",
            "交易总笔数(配对)": total_trades,
            "胜率 (Win Rate)": f"{win_rate * 100:.2f}%",
            "盈亏比 (PnL Ratio)": f"{pnl_ratio:.2f}",
            "平均持仓时间": f"{avg_hold:.2f} 小时",
            "年化收益率 (Ann. Return)": f"{annual_return * 100:.2f}%",
            "最大回撤 (Max Drawdown)": f"{max_drawdown * 100:.2f}%",
            "夏普比率 (Sharpe Ratio)": f"{sharpe_ratio:.2f}",
            "卡玛比率 (Calmar Ratio)": f"{calmar_ratio:.2f}",
        }

        acct_pnl = self.account_summary.get('total_pnl')
        if acct_pnl is not None:
            self.metrics["账户累计平仓盈亏"] = f"￥{acct_pnl:,.2f}"
            gross_paired = self.match_df['gross_pnl'].sum() if not self.match_df.empty else 0.0
            diff = gross_paired - acct_pnl
            self.metrics["配对毛利 vs 账户(差异)"] = f"￥{diff:,.2f}"

        rollover_cnt = self.account_summary.get('rollover_count')
        if rollover_cnt is not None:
            self.metrics["主力换月次数"] = rollover_cnt
        rollover_fee = self.account_summary.get('rollover_commission')
        if rollover_fee is not None and rollover_fee > 0:
            self.metrics["换月手续费合计"] = f"￥{rollover_fee:,.2f}"

        if self.unmatched_close_volume > 0:
            self.metrics["未配对平仓量"] = f"{self.unmatched_close_volume} 手"

    def _plot_trade_dna(self):
        """生成交易 DNA 四宫格图"""
        if self.match_df.empty:
            return

        df_match = self.match_df
        df_win = df_match[df_match['net_pnl'] > 0]
        df_loss = df_match[df_match['net_pnl'] <= 0]

        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=("盈亏金额分布", "持仓时间分布 (小时)",
                            "开平仓价位散点 (打靶图)", "盈亏 vs 持仓时间"),
            horizontal_spacing=0.1, vertical_spacing=0.15
        )

        fig.add_trace(go.Histogram(x=df_win['net_pnl'], name='盈利单', marker_color='red', opacity=0.7), row=1, col=1)
        fig.add_trace(go.Histogram(x=df_loss['net_pnl'], name='亏损单', marker_color='green', opacity=0.7), row=1, col=1)
        fig.add_trace(go.Histogram(x=df_win['hold_time_hours'], name='盈利单持仓时长', marker_color='red', opacity=0.6), row=1, col=2)
        fig.add_trace(go.Histogram(x=df_loss['hold_time_hours'], name='亏损单持仓时长', marker_color='green', opacity=0.6), row=1, col=2)

        fig.add_trace(go.Scatter(
            x=df_win['open_price'], y=df_win['close_price'], mode='markers', name='盈利靶点',
            marker=dict(color='red', size=8, line=dict(width=1, color='darkred')),
            text=df_win['direction'] + " | Net PnL: " + df_win['net_pnl'].round(2).astype(str)
        ), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=df_loss['open_price'], y=df_loss['close_price'], mode='markers', name='亏损靶点',
            marker=dict(color='green', size=8, symbol='x'),
            text=df_loss['direction'] + " | Net PnL: " + df_loss['net_pnl'].round(2).astype(str)
        ), row=2, col=1)

        fig.add_trace(go.Scatter(
            x=df_match['hold_time_hours'], y=df_match['net_pnl'], mode='markers', name='盈亏与时间关系',
            marker=dict(
                color=df_match['net_pnl'], colorscale='RdYlGn', reversescale=True,
                size=df_match['volume'], sizemode='area', sizeref=2. * max(df_match['volume']) / (20. ** 2), sizemin=4
            ),
            text="手数: " + df_match['volume'].astype(str)
        ), row=2, col=2)

        fig.update_layout(title_text=f"{self.symbol} 策略 DNA 透视面板", height=900, template='plotly_white', barmode='overlay')
        file_path = os.path.join(self.output_dir, '2_trade_dna.png')
        fig.write_image(file_path, scale=2)
        print(f"📊 分布图已保存至: {file_path}")
        fig.show()

    def _get_equity_series(self):
        """优先使用引擎盯市权益曲线"""
        if self.equity_df is not None and not self.equity_df.empty:
            eq = self.equity_df.sort_values('datetime').drop_duplicates('datetime', keep='last')
            return eq['datetime'].tolist(), eq['equity'].tolist()

        if self.match_df.empty:
            return self.price_df['datetime'].tolist(), [self.initial_capital] * len(self.price_df)

        equity_x = [self.price_df['datetime'].iloc[0]] + self.match_df['close_time'].tolist()
        equity_y = [self.initial_capital] + (self.initial_capital + self.match_df['net_pnl'].cumsum()).tolist()
        return equity_x, equity_y

    def _calculate_benchmark(self):
        """计算 Benchmark 曲线"""
        calc_df = self.price_df.drop(columns=['datetime']) if 'datetime' in self.price_df.columns else self.price_df
        returns_df = calc_df.pct_change().fillna(0)

        if self.symbol == 'MULTI':
            benchmark_returns = returns_df.mean(axis=1)
        else:
            target_cols = [col for col in calc_df.columns if self.symbol.lower() in col.lower()]
            if target_cols:
                benchmark_returns = returns_df[target_cols[0]]
            else:
                benchmark_returns = pd.Series(0, index=returns_df.index)

        benchmark_curve = self.initial_capital * (1 + benchmark_returns).cumprod()
        return pd.Series(benchmark_curve.values, index=self.price_df['datetime'])

    def _plot_equity_and_markers(self):
        """图表主入口"""
        if self.symbol == 'MULTI':
            self._plot_multi_factor()
        else:
            self._plot_single_asset()

    def _plot_multi_factor(self):
        """多因子组合画图"""
        fig = go.Figure()
        benchmark_curve = self._calculate_benchmark()
        equity_x, equity_y = self._get_equity_series()

        fig.add_trace(go.Scatter(x=equity_x, y=equity_y, mode='lines', name=f'{self.strategy_name} 净值',
                                 line=dict(color='blue', width=2), fill='tozeroy', fillcolor='rgba(0,0,255,0.1)'))
        fig.add_trace(go.Scatter(x=benchmark_curve.index, y=benchmark_curve.values, mode='lines', name='全市场等权基准 (1/N)',
                                 line=dict(color='gray', width=2, dash='dash')))

        fig.update_layout(title=f"多因子组合净值 vs 等权基准", yaxis_title="账户净值 (￥)",
                          hovermode="x unified", template="plotly_white", height=600)

        file_path = os.path.join(self.output_dir, '1_equity_and_benchmark.png')
        fig.write_image(file_path, scale=2)
        print(f"📊 多因子资金曲线已保存至: {file_path}")
        fig.show()

    def _plot_single_asset(self):
        """单品种画图"""
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])

        calc_df = self.price_df.drop(columns=['datetime']) if 'datetime' in self.price_df.columns else self.price_df
        target_cols = [col for col in calc_df.columns if self.symbol.lower() in col.lower()]
        price_col = target_cols[0] if target_cols else calc_df.columns[0]

        fig.add_trace(go.Scatter(x=self.price_df['datetime'], y=calc_df[price_col],
                                 mode='lines', name='Price', line=dict(color='gray', width=1)), row=1, col=1)

        df_trades = pd.DataFrame([{
            'time': t.trade_time, 'price': t.price, 'volume': t.volume,
            'is_open': t.offset == Offset.OPEN, 'is_long': t.direction == Direction.LONG
        } for t in self.trades if not getattr(t, 'is_rollover', False)])

        if not df_trades.empty:
            open_long = df_trades[df_trades['is_open'] & df_trades['is_long']]
            fig.add_trace(go.Scatter(x=open_long['time'], y=open_long['price'], mode='markers', name='买入(开多)',
                                     marker=dict(symbol='triangle-up', size=12, color='red')), row=1, col=1)

            close_long = df_trades[~df_trades['is_open'] & ~df_trades['is_long']]
            fig.add_trace(go.Scatter(x=close_long['time'], y=close_long['price'], mode='markers', name='平仓(平多)',
                                     marker=dict(symbol='square', size=10, color='green')), row=1, col=1)

            open_short = df_trades[df_trades['is_open'] & ~df_trades['is_long']]
            fig.add_trace(go.Scatter(x=open_short['time'], y=open_short['price'], mode='markers', name='卖空(开空)',
                                     marker=dict(symbol='triangle-down', size=12, color='green')), row=1, col=1)

            close_short = df_trades[~df_trades['is_open'] & df_trades['is_long']]
            fig.add_trace(go.Scatter(x=close_short['time'], y=close_short['price'], mode='markers', name='平仓(平空)',
                                     marker=dict(symbol='square', size=10, color='red')), row=1, col=1)

        equity_x, equity_y = self._get_equity_series()
        fig.add_trace(go.Scatter(x=equity_x, y=equity_y, mode='lines', name='净值(Net Equity)',
                                 line=dict(color='blue', width=2), fill='tozeroy', fillcolor='rgba(0,0,255,0.1)'), row=2, col=1)

        benchmark_curve = self._calculate_benchmark()
        fig.add_trace(go.Scatter(x=benchmark_curve.index, y=benchmark_curve.values, mode='lines', name='标的自身基准',
                                 line=dict(color='orange', width=1, dash='dash')), row=2, col=1)

        fig.update_layout(title=f'{self.strategy_name} on {self.symbol} - 进出场标记与资金曲线',
                          height=800, template='plotly_white', hovermode='x unified')

        file_path = os.path.join(self.output_dir, '1_equity_and_trades.png')
        fig.write_image(file_path, scale=2)
        print(f"📊 走势图已保存至: {file_path}")
        fig.show()

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

        self._plot_equity_and_markers()
        self._plot_trade_dna()

        print(f"✅ 全部分析报告已成功生成至文件夹: {self.output_dir}")
