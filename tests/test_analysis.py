import unittest

from analyze_lobster import quantile, replay
from order_book import OrderBook


class ReplayAnalysisTests(unittest.TestCase):
    def test_replay_calculates_vwap_shortfall_and_partial_fill(self):
        cost, fill = replay([(10100, 2), (10200, 2)], "buy", 3, 10000)
        self.assertAlmostEqual(cost, ((10100 * 2 + 10200) / 3 - 10000) / 10000 * 10000)
        self.assertAlmostEqual(fill, 1.0)

    def test_replay_reports_unfilled_fraction_and_sell_cost(self):
        cost, fill = replay([(9900, 2)], "sell", 4, 10000)
        self.assertAlmostEqual(cost, 100.0)
        self.assertAlmostEqual(fill, 0.5)

    def test_quantile_interpolates(self):
        self.assertAlmostEqual(quantile([1, 3, 7, 9], 0.5), 5.0)

    def test_snapshot_sweep_matches_order_book_execution(self):
        asks = [(10100, 2), (10200, 2)]
        expected_cost, expected_fill = replay(asks, "buy", 3, 10000)
        book = OrderBook()
        for index, (price, size) in enumerate(asks):
            book.limit_order(f"ask-{index}", "sell", price, size)
        _, unfilled = book.market_order("taker", "buy", 3)
        vwap = sum(trade.price * trade.quantity for trade in book.trades) / sum(trade.quantity for trade in book.trades)
        actual_cost = (vwap - 10000) / 10000 * 10000
        self.assertAlmostEqual(actual_cost, expected_cost)
        self.assertAlmostEqual((3 - unfilled) / 3, expected_fill)


if __name__ == "__main__":
    unittest.main()
