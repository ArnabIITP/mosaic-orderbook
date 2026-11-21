import unittest
from collections import Counter

import numpy as np

from analyze_wselob import _reconstruct_day


class WselobReconstructionTests(unittest.TestCase):
    def test_add_modify_delete_and_refresh_reconstruct_displayed_levels(self):
        dtype = [
            ("time", "i8"), ("order_date", "i8"), ("price", "i8"),
            ("agg_volume", "i8"),
            ("volume", "i8"), ("order_id", "i8"), ("side", "i1"),
            ("order_type", "S2"), ("action_type", "S1"),
        ]
        rows = np.array([
            (0, -1, 0, 0, 0, 0, -1, b"-1", b"F"),
            (1, 10, 10000, 10, 10, 1, 1, b"2", b"A"),
            (2, 10, 10200, 20, 20, 2, 2, b"2", b"A"),
            (3, 10, 10000, 7, 7, 1, 1, b"2", b"M"),
            (4, 10, 10200, 0, 0, 2, 2, b"2", b"D"),
            (5, 10, 10300, 15, 15, 3, 2, b"2", b"A"),
        ], dtype=dtype)
        diagnostics = Counter()
        snapshots = _reconstruct_day(rows, stride=1, date="20170102", diagnostics=diagnostics)

        self.assertEqual(len(snapshots), 3)
        self.assertEqual(snapshots[0]["bids"], [(10000, 10)])
        self.assertEqual(snapshots[0]["asks"], [(10200, 20)])
        self.assertEqual(snapshots[1]["bids"], [(10000, 7)])
        self.assertEqual(snapshots[2]["asks"], [(10300, 15)])
        self.assertEqual(diagnostics["full_refreshes"], 1)
        self.assertEqual(diagnostics["invalid_or_one_sided_snapshots"], 3)
        self.assertEqual(diagnostics["aggregate_volume_mismatches"], 0)

    def test_modify_sentinels_preserve_fields_and_priced_market_to_limit_rests(self):
        dtype = [
            ("time", "i8"), ("order_date", "i8"), ("price", "i8"),
            ("agg_volume", "i8"), ("volume", "i8"), ("order_id", "i8"),
            ("side", "i1"), ("order_type", "S2"), ("action_type", "S1"),
        ]
        rows = np.array([
            (0, -1, 0, 0, 0, 0, -1, b"-1", b"F"),
            (1, 10, 10000, 10, 10, 1, 1, b"2", b"A"),
            (2, 10, 10200, 5, 5, 2, 2, b"K", b"A"),
            (3, 10, -1, 0, -1, 1, -1, b"-1", b"M"),
        ], dtype=dtype)
        diagnostics = Counter()
        snapshots = _reconstruct_day(rows, stride=1, date="20170102", diagnostics=diagnostics)

        self.assertEqual(snapshots[-1]["bids"], [(10000, 10)])
        self.assertEqual(snapshots[-1]["asks"], [(10200, 5)])


if __name__ == "__main__":
    unittest.main()
