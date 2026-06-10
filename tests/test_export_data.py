# -*- coding: utf-8 -*-
import sys
import types
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if "clickhouse_driver" not in sys.modules:
    clickhouse_driver = types.ModuleType("clickhouse_driver")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

    clickhouse_driver.Client = _Client
    sys.modules["clickhouse_driver"] = clickhouse_driver

from export_data import _ensure_datetime_column


class ExportDataTest(unittest.TestCase):
    def test_ensure_datetime_column_preserves_wide_index(self):
        df = pd.DataFrame(
            {"rb_close": [3500.0, 3510.0]},
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )

        result = _ensure_datetime_column(df)

        self.assertEqual(result.columns[0], "datetime")
        self.assertEqual(result.iloc[0]["datetime"], pd.Timestamp("2024-01-02"))
        self.assertEqual(result.iloc[1]["rb_close"], 3510.0)


if __name__ == "__main__":
    unittest.main()
