import tempfile
import unittest
from pathlib import Path

import pandas as pd

from analyzer.performance import StrategyAnalyzer
from broker.order import Direction, Offset, Trade
from frontend_index import build_html_dashboard


class FrontendReportTest(unittest.TestCase):
    def test_generated_report_is_self_contained(self):
        datetimes = pd.to_datetime(['2026-01-02', '2026-01-05'])
        price_df = pd.DataFrame({('close', 'rb'): [100.0, 110.0]}, index=datetimes)
        price_df.columns = pd.MultiIndex.from_tuples(price_df.columns)
        trades = [
            Trade('rb', Direction.LONG, Offset.OPEN, 1, 100.0, datetimes[0], 0.0, 0.0, 'open'),
            Trade('rb', Direction.SHORT, Offset.CLOSE, 1, 110.0, datetimes[1], 0.0, 0.0, 'close'),
        ]
        analyzer = StrategyAnalyzer(
            trades=trades,
            price_df=price_df,
            initial_capital=1_000_000.0,
            symbol='MULTI',
            freq='1d',
            strategy_name='OfflineReportTest',
            equity_df=pd.DataFrame([
                {
                    'datetime': datetimes[0],
                    'trading_date': datetimes[0],
                    'equity': 1_000_000.0,
                    'margin_used': 10_000.0,
                },
                {
                    'datetime': datetimes[1],
                    'trading_date': datetimes[1],
                    'equity': 1_000_100.0,
                    'margin_used': 0.0,
                },
            ]),
        )
        analyzer._match_trades_fifo()
        analyzer._calculate_metrics()

        with tempfile.TemporaryDirectory() as output_dir:
            analyzer.output_dir = output_dir
            report_path = build_html_dashboard(
                analyzer, open_browser=False, start_config_ui=False
            )
            html = Path(report_path).read_text(encoding='utf-8')

        self.assertNotIn('<script src="http', html)
        self.assertNotIn('cdn.tailwindcss.com', html)
        self.assertIn('html2canvas', html)
        self.assertIn('window.jspdf', html)
        self.assertIn('Plotly.newPlot', html)
        self.assertIn('.xl\\:grid-cols-5', html)
        self.assertIn('.md\\:grid-cols-3', html)
        self.assertIn('report-table-scroll', html)
        self.assertIn('metrics-table', html)
        self.assertIn('metrics-table-fit', html)
        self.assertIn('保证金占用率 (Margin Utilization)', html)


if __name__ == '__main__':
    unittest.main()
