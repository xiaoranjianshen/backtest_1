# -*- coding: utf-8 -*-
import unittest
from datetime import datetime

import pandas as pd

from analyzer.performance import StrategyAnalyzer, _trading_session_dates
from broker.order import Direction, Offset, Trade


class AnalyzerMetricsTest(unittest.TestCase):
    def test_intraday_metric_dates_match_the_following_trading_day(self):
        timestamps = pd.Series(pd.to_datetime([
            "2026-07-13 21:00:00",
            "2026-07-14 01:00:00",
            "2026-07-14 09:00:00",
        ]))

        session_dates = _trading_session_dates(timestamps, "1m")

        self.assertEqual(session_dates.iloc[0], session_dates.iloc[1])
        self.assertEqual(session_dates.iloc[1], session_dates.iloc[2])
        self.assertEqual(session_dates.iloc[0], pd.Timestamp("2026-07-14"))

    def test_total_daily_metrics_include_first_session_return(self):
        datetimes = pd.to_datetime(["2026-01-02 15:00:00", "2026-01-05 15:00:00"])
        price_df = pd.DataFrame({("close", "rb"): [100.0, 100.0]}, index=datetimes)
        price_df.columns = pd.MultiIndex.from_tuples(price_df.columns)
        equity_df = pd.DataFrame([
            {"datetime": datetimes[0], "trading_date": "2026-01-02", "equity": 1_100_000.0, "position_notional": 0.0},
            {"datetime": datetimes[1], "trading_date": "2026-01-05", "equity": 1_100_000.0, "position_notional": 0.0},
        ])
        analyzer = StrategyAnalyzer(
            trades=[], price_df=price_df, initial_capital=1_000_000.0,
            symbol="MULTI", freq="1m", strategy_name="FirstDayReturnTest",
            equity_df=equity_df,
        )

        metrics = analyzer._calc_single_metrics(pd.DataFrame(), "MULTI", "FirstDayReturnTest", is_total=True)

        self.assertEqual(metrics["逐日胜率"], "50.00%")
        self.assertNotEqual(metrics["年化Sharpe"], "0.00")

    def test_drawdown_includes_loss_before_first_equity_snapshot(self):
        datetimes = pd.to_datetime(["2026-01-02 15:00:00", "2026-01-05 15:00:00"])
        price_df = pd.DataFrame({("close", "rb"): [100.0, 100.0]}, index=datetimes)
        price_df.columns = pd.MultiIndex.from_tuples(price_df.columns)
        analyzer = StrategyAnalyzer(
            trades=[], price_df=price_df, initial_capital=1_000_000.0,
            symbol="MULTI", freq="1d", strategy_name="FirstDayDrawdownTest",
            equity_df=pd.DataFrame([
                {"datetime": datetimes[0], "trading_date": datetimes[0], "equity": 900_000.0},
                {"datetime": datetimes[1], "trading_date": datetimes[1], "equity": 900_000.0},
            ]),
        )

        metrics = analyzer._calc_single_metrics(
            pd.DataFrame(), "MULTI", "FirstDayDrawdownTest", is_total=True
        )

        self.assertEqual(metrics["最大回撤率"], "-10.00%")

    def test_multi_asset_curve_exposes_sector_controls(self):
        datetimes = pd.to_datetime(["2026-01-02", "2026-01-05"])
        trades = [
            Trade("rb", Direction.LONG, Offset.OPEN, 1, 100.0, datetimes[0], 0.0, 0.0, "rb-open"),
            Trade("rb", Direction.SHORT, Offset.CLOSE, 1, 110.0, datetimes[1], 0.0, 0.0, "rb-close"),
            Trade("au", Direction.LONG, Offset.OPEN, 1, 500.0, datetimes[0], 0.0, 0.0, "au-open"),
            Trade("au", Direction.SHORT, Offset.CLOSE, 1, 510.0, datetimes[1], 0.0, 0.0, "au-close"),
        ]
        price_df = pd.DataFrame(
            {("close", "rb"): [100.0, 110.0], ("close", "au"): [500.0, 510.0]},
            index=datetimes,
        )
        price_df.columns = pd.MultiIndex.from_tuples(price_df.columns)
        analyzer = StrategyAnalyzer(
            trades=trades, price_df=price_df, initial_capital=1_000_000.0,
            symbol="MULTI", freq="1d", strategy_name="SectorControlTest",
            equity_df=pd.DataFrame([
                {"datetime": datetimes[0], "trading_date": datetimes[0], "equity": 1_000_000.0},
                {"datetime": datetimes[1], "trading_date": datetimes[1], "equity": 1_000_020.0},
            ]),
        )
        analyzer._match_trades_fifo()

        html = analyzer.get_multi_asset_pnl_curves_html_div()

        self.assertIn("pnl-curve-sector-toggle", html)
        self.assertIn("data-sector=", html)
        self.assertIn("pnl-curve-mode", html)

    def test_daily_metric_dates_keep_daily_bar_date(self):
        timestamps = pd.Series(pd.to_datetime([
            "2026-07-10 00:00:00",
            "2026-07-11 00:00:00",
        ]))

        session_dates = _trading_session_dates(timestamps, "1d")

        self.assertEqual(session_dates.tolist(), timestamps.tolist())

    def test_trade_days_and_market_days_are_reported_separately(self):
        equity_df = pd.DataFrame([
            {"datetime": "2026-04-01 09:00:00", "equity": 1_000_000.0, "position_notional": 0.0},
            {"datetime": "2026-04-01 15:00:00", "equity": 1_000_100.0, "position_notional": 0.0},
            {"datetime": "2026-04-03 15:00:00", "equity": 1_000_050.0, "position_notional": 0.0},
            {"datetime": "2026-04-05 15:00:00", "equity": 1_000_200.0, "position_notional": 0.0},
        ])
        price_df = pd.DataFrame(index=pd.to_datetime([
            "2026-04-01 09:00:00",
            "2026-04-03 15:00:00",
            "2026-04-05 15:00:00",
        ]))
        match_df = pd.DataFrame([
            {"symbol": "au", "close_time": "2026-04-03 10:00:00", "net_pnl": -50.0},
            {"symbol": "au", "close_time": "2026-04-05 10:00:00", "net_pnl": 150.0},
        ])

        analyzer = StrategyAnalyzer(
            trades=[],
            price_df=price_df,
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="tick",
            strategy_name="TestStrategy",
            equity_df=equity_df,
        )

        metrics = analyzer._calc_single_metrics(match_df, "MULTI", "TestStrategy", is_total=True)

        self.assertEqual(metrics["交易次数"], 2)
        self.assertEqual(metrics["成交日数"], 2)
        self.assertEqual(metrics["行情日数"], 3)
        self.assertNotIn("交易日数", metrics)

    def test_fund_flow_commission_accumulates_by_trade_time(self):
        equity_df = pd.DataFrame([
            {"datetime": "2026-05-07 09:00:00", "equity": 1_000_000.0, "position_notional": 0.0},
            {"datetime": "2026-05-07 09:00:01", "equity": 1_000_000.0, "position_notional": 0.0},
            {"datetime": "2026-05-07 21:00:49", "equity": 999_980.0, "position_notional": 0.0},
            {"datetime": "2026-05-07 21:00:50", "equity": 999_950.0, "position_notional": 0.0},
        ])
        price_df = pd.DataFrame(index=pd.to_datetime(equity_df["datetime"]))
        trade = Trade(
            symbol="ag",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=19990.0,
            trade_time=datetime(2026, 5, 7, 21, 0, 49),
            commission=29.97,
            slippage_cost=0.0,
            order_id="test-order",
        )

        analyzer = StrategyAnalyzer(
            trades=[trade],
            price_df=price_df,
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="tick",
            strategy_name="TestStrategy",
            equity_df=equity_df,
        )

        fund_flow = analyzer.get_fund_flow_df()
        commission_col = fund_flow.columns[-1]

        self.assertEqual(float(fund_flow.iloc[0][commission_col]), 0.0)
        self.assertEqual(float(fund_flow.iloc[1][commission_col]), 0.0)
        self.assertEqual(float(fund_flow.iloc[2][commission_col]), 29.97)
        self.assertEqual(float(fund_flow.iloc[3][commission_col]), 29.97)

    def test_signal_diagnostics_infers_target_style_signal_direction(self):
        df = pd.DataFrame([
            {"signal": None, "target_weight": 0.05, "target_margin_pct": None, "target_net": None, "position_mode": None},
            {"signal": None, "target_weight": -0.05, "target_margin_pct": None, "target_net": None, "position_mode": None},
            {"signal": None, "target_weight": 0.0, "target_margin_pct": None, "target_net": None, "position_mode": None},
            {"signal": None, "target_weight": None, "target_margin_pct": 0.08, "target_net": None, "position_mode": None},
            {"signal": None, "target_weight": None, "target_margin_pct": None, "target_net": -3, "position_mode": None},
            {"signal": None, "target_weight": None, "target_margin_pct": None, "target_net": None, "position_mode": "flat"},
            {"signal": 1, "target_weight": -0.05, "target_margin_pct": None, "target_net": None, "position_mode": None},
        ])

        inferred = StrategyAnalyzer._infer_effective_signal(df).tolist()

        self.assertEqual(inferred, [1, -1, 0, 1, -1, 0, 1])

    def test_signal_diagnostics_infers_signal_score_for_ic(self):
        df = pd.DataFrame([
            {"signal": 1, "signal_score": 0.83, "target_weight": -0.05, "target_margin_pct": None, "target_net": None, "size_scale": None, "risk_pct": None},
            {"signal": 1, "signal_score": None, "target_weight": 0.05, "target_margin_pct": None, "target_net": None, "size_scale": None, "risk_pct": None},
            {"signal": -1, "signal_score": None, "target_weight": None, "target_margin_pct": 0.08, "target_net": None, "size_scale": None, "risk_pct": None},
            {"signal": None, "signal_score": None, "target_weight": None, "target_margin_pct": None, "target_net": -3, "size_scale": None, "risk_pct": None},
            {"signal": -1, "signal_score": None, "target_weight": None, "target_margin_pct": None, "target_net": None, "size_scale": 0.5, "risk_pct": None},
            {"signal": 1, "signal_score": None, "target_weight": None, "target_margin_pct": None, "target_net": None, "size_scale": None, "risk_pct": 0.01},
        ])

        scores = StrategyAnalyzer._infer_signal_score(df).tolist()

        self.assertEqual(scores, [0.83, 0.05, 0.08, -3.0, -0.5, 0.01])

    def test_signal_ic_uses_raw_forward_return_not_directional_return(self):
        analyzer = StrategyAnalyzer(
            trades=[],
            price_df=pd.DataFrame(index=pd.to_datetime(["2026-01-01"])),
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="1d",
            strategy_name="TestStrategy",
            equity_df=pd.DataFrame([{"datetime": "2026-01-01", "equity": 1_000_000.0}]),
        )
        entry_df = pd.DataFrame({
            "signal_score": [-1.0, 0.0, 1.0],
            "fwd_1_bar_raw_return": [-0.02, 0.0, 0.02],
            "fwd_1_bar_return": [0.02, 0.0, 0.02],
        })

        ic_df = analyzer._build_signal_ic_df(entry_df, [1])

        self.assertAlmostEqual(float(ic_df.loc[0, "ic"]), 1.0)
        self.assertAlmostEqual(float(ic_df.loc[0, "rank_ic"]), 1.0)

    def test_signal_diagnostics_html_exposes_scope_controls_and_event_download(self):
        dates = pd.date_range("2026-01-01", periods=8, freq="D")
        price_df = pd.DataFrame(
            {
                ("close", "rb"): [100, 102, 104, 106, 108, 110, 112, 114],
                ("close", "au"): [500, 498, 496, 494, 492, 490, 488, 486],
            },
            index=dates,
        )
        price_df.columns = pd.MultiIndex.from_tuples(price_df.columns)
        signal_records = [
            {"datetime": dates[0], "symbol": "rb", "signal": 1, "reason": "rb_entry", "current_net": 0, "price": 100, "signal_score": 0.8},
            {"datetime": dates[1], "symbol": "rb", "signal": 0, "reason": "rb_exit", "current_net": 1, "price": 102, "signal_score": 0.0},
            {"datetime": dates[0], "symbol": "au", "signal": -1, "reason": "au_short", "current_net": 0, "price": 500, "signal_score": -0.7},
        ]
        analyzer = StrategyAnalyzer(
            trades=[],
            price_df=price_df,
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="1d",
            strategy_name="SignalHtmlTestStrategy",
            equity_df=pd.DataFrame([{"datetime": dates[0], "equity": 1_000_000.0}]),
            signal_records=signal_records,
        )

        html = analyzer.get_signal_diagnostics_html_div()

        self.assertIn("signal-sector-select", html)
        self.assertIn("signal-product-select", html)
        self.assertIn("信号检测范围 (Signal Scope)", html)
        self.assertIn("下载完整信号明细 CSV", html)
        self.assertIn("板块统计 (By Sector)", html)

    def test_leverage_chart_uses_recorded_position_notional(self):
        equity_df = pd.DataFrame([
            {"datetime": "2026-05-07 09:00:00", "equity": 1_000_000.0, "position_notional": 0.0},
            {"datetime": "2026-05-07 09:00:01", "equity": 1_000_000.0, "position_notional": 200_000.0},
        ])
        trade = Trade(
            symbol="rb",
            direction=Direction.LONG,
            offset=Offset.OPEN,
            volume=1,
            price=3500.0,
            trade_time=datetime(2026, 5, 7, 9, 0, 0),
            commission=3.5,
            slippage_cost=0.0,
            order_id="test-order",
        )
        analyzer = StrategyAnalyzer(
            trades=[trade],
            price_df=pd.DataFrame(index=pd.to_datetime(equity_df["datetime"])),
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="tick",
            strategy_name="LeverageHtmlTestStrategy",
            equity_df=equity_df,
        )

        html = analyzer.get_leverage_and_position_html_div()

        self.assertIn("总持仓名义本金", html)
        self.assertIn("实时总杠杆率", html)

    def test_leverage_chart_uses_directional_position_notional_when_available(self):
        equity_df = pd.DataFrame([
            {
                "datetime": "2026-05-07 09:00:00",
                "equity": 1_000_000.0,
                "position_notional": 0.0,
                "long_position_notional": 0.0,
                "short_position_notional": 0.0,
            },
            {
                "datetime": "2026-05-07 09:00:01",
                "equity": 1_000_000.0,
                "position_notional": 500_000.0,
                "long_position_notional": 200_000.0,
                "short_position_notional": 300_000.0,
            },
        ])
        trade = Trade(
            symbol="rb",
            direction=Direction.SHORT,
            offset=Offset.OPEN,
            volume=1,
            price=3500.0,
            trade_time=datetime(2026, 5, 7, 9, 0, 0),
            commission=3.5,
            slippage_cost=0.0,
            order_id="test-order",
        )
        analyzer = StrategyAnalyzer(
            trades=[trade],
            price_df=pd.DataFrame(index=pd.to_datetime(equity_df["datetime"])),
            initial_capital=1_000_000.0,
            symbol="MULTI",
            freq="tick",
            strategy_name="DirectionalLeverageHtmlTestStrategy",
            equity_df=equity_df,
        )

        html = analyzer.get_leverage_and_position_html_div()

        self.assertIn("多头持仓名义本金", html)
        self.assertIn("空头持仓名义本金", html)


if __name__ == "__main__":
    unittest.main()
