import os
import tempfile
import time
import unittest
from unittest.mock import patch

import pandas as pd

from data_feed import ch_loader
from data_feed.ch_loader import ClickHouseLoader


class DataCacheTest(unittest.TestCase):
    def setUp(self):
        self.loader = ClickHouseLoader.__new__(ClickHouseLoader)

    def test_cache_identity_includes_source_and_normalizes_symbol_order(self):
        with tempfile.TemporaryDirectory() as cache_dir, patch.object(ch_loader, 'CACHE_DIR', cache_dir):
            with patch.object(ch_loader, 'CH_HOST', 'source-a'):
                first = self.loader._build_cache_path(
                    ['rb', 'au'], '2026-01-01', '2026-02-01', '1d', 'main'
                )
                reordered = self.loader._build_cache_path(
                    ['au', 'rb'], '2026-01-01', '2026-02-01', '1d', 'main'
                )
            with patch.object(ch_loader, 'CH_HOST', 'source-b'):
                other_source = self.loader._build_cache_path(
                    ['rb', 'au'], '2026-01-01', '2026-02-01', '1d', 'main'
                )

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, other_source)

    def test_expired_cache_is_reloaded_and_replaced(self):
        frame = pd.DataFrame({
            'symbol': ['rb'],
            'datetime': pd.to_datetime(['2026-01-05']),
            'open': [100.0], 'high': [101.0], 'low': [99.0], 'close': [100.0],
            'volume': [1],
        })
        with tempfile.TemporaryDirectory() as cache_dir, \
                patch.object(ch_loader, 'CACHE_DIR', cache_dir), \
                patch.dict(os.environ, {'BACKTEST_CACHE_TTL_SECONDS': '1'}), \
                patch.object(self.loader, '_fetch_from_db', return_value=frame) as fetch:
            cache_path = self.loader._build_cache_path(
                ['rb'], '2026-01-01', '2026-02-01', '1d', 'main'
            )
            frame.to_parquet(cache_path)
            old_time = time.time() - 60
            os.utime(cache_path, (old_time, old_time))

            result = self.loader.get_data(
                ['rb'], '2026-01-01', '2026-02-01', '1d', 'main'
            )

            fetch.assert_called_once()
            self.assertEqual(len(result), 1)
            self.assertTrue(self.loader._cache_is_fresh(cache_path))


if __name__ == '__main__':
    unittest.main()
