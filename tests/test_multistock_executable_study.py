import unittest

import pandas as pd

from multistock_executable_study import _date_split, _sweep, _trade_metrics


class MultiStockExecutableStudyTests(unittest.TestCase):
    def test_sweep_requires_requested_quantity_and_prices_depth(self):
        self.assertAlmostEqual(_sweep([(101, 2), (102, 5)], 6), (2 * 101 + 4 * 102) / 6)
        self.assertIsNone(_sweep([(101, 2)], 3))

    def test_date_split_is_chronological_and_keeps_final_block(self):
        dates = [f"2017{i:04d}" for i in range(1, 41)]
        split = _date_split(dates)
        self.assertEqual(len(split["train"]), 28)
        self.assertEqual(len(split["validation"]), 6)
        self.assertEqual(len(split["test"]), 6)
        self.assertLess(split["train"][-1], split["validation"][0])
        self.assertLess(split["validation"][-1], split["test"][0])

    def test_policy_metrics_skip_overlapping_positions_per_ticker(self):
        frame = pd.DataFrame([
            {"ticker": ticker, "date": "20170102", "timestamp": timestamp,
             "future_timestamp": exit_time, "net_up_bps": net, "net_down_bps": -net}
            for ticker, rows in {
                "KGHM": [(0.0, 10.0, 1.0), (5.0, 15.0, 3.0), (11.0, 21.0, 5.0)],
                "PZU": [(0.0, 10.0, 2.0), (5.0, 15.0, 4.0), (11.0, 21.0, 6.0)],
            }.items()
            for timestamp, exit_time, net in rows
        ])
        metrics = _trade_metrics(frame, frame.index.map(lambda _: "up").to_numpy())
        self.assertEqual(metrics["non_overlapping_round_trips"], 4)
        self.assertEqual(metrics["active_test_dates"], 1)
        self.assertEqual(metrics["per_ticker_non_overlapping_trips"]["KGHM"], 2)
        self.assertEqual(metrics["per_ticker_non_overlapping_trips"]["PZU"], 2)
        self.assertAlmostEqual(metrics["mean_net_bps_per_trade"], 3.5)


if __name__ == "__main__":
    unittest.main()
