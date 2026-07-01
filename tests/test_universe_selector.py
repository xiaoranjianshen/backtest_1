# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from strategy.common.universe import (  # noqa: E402
    DataFrameUniverseSelector,
    MappingUniverseSelector,
    UniverseSelectionEntry,
    selection_metrics,
)


class UniverseSelectorTest(unittest.TestCase):
    def test_mapping_selector_normalizes_date_and_symbol(self):
        selector = MappingUniverseSelector(
            {
                "2025-08-04": {
                    "KQ.m@DCE.c2601": {"rank": 1, "score": 0.82, "weight": 0.7},
                    "CF509": {"rank": 2, "score": 0.61, "weight": 0.3},
                }
            },
            name="demo_selector",
        )

        self.assertTrue(selector.contains(pd.Timestamp("2025-08-04 09:30"), "c2601"))
        self.assertTrue(selector.contains("2025-08-04", "cf509"))
        self.assertEqual(selector.entry("2025-08-04", "c2601").rank, 1)
        self.assertEqual(selector.selected_symbols("2025-08-04"), ["c2601", "cf509"])

    def test_dataframe_selector_keeps_extra_metrics(self):
        frame = pd.DataFrame(
            [
                {
                    "trade_date": "2025-08-04",
                    "symbol": "c2601",
                    "rank": 1,
                    "score": 0.82,
                    "weight": 0.6,
                    "hit_rate": 0.63,
                }
            ]
        )
        selector = DataFrameUniverseSelector(frame, name="model_a")
        entry = selector.entry("2025-08-04", "c2601")

        self.assertIsInstance(entry, UniverseSelectionEntry)
        self.assertEqual(entry.model_name, "model_a")
        self.assertEqual(entry.meta["hit_rate"], 0.63)

        metrics = selection_metrics(entry)
        self.assertEqual(metrics["selector_weight"], 0.6)
        self.assertEqual(metrics["indicator_selector_weight"], 0.6)
        self.assertEqual(metrics["selector_hit_rate"], 0.63)

    def test_entry_lookup_falls_back_from_contract_to_product(self):
        selector = MappingUniverseSelector(
            {"2025-08-04": {"c": {"rank": 1, "score": 0.9, "weight": 1.0}}},
            name="product_selector",
        )

        entry = selector.entry("2025-08-04", "c2601")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.normalized_symbol, "c")


if __name__ == "__main__":
    unittest.main()
