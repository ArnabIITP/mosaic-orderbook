import unittest

from order_book import OrderBook


class OrderBookTests(unittest.TestCase):
    def test_price_then_time_priority(self):
        book = OrderBook()
        first, _ = book.limit_order("first", "sell", 101, 3)
        book.limit_order("better", "sell", 100, 2)
        book.limit_order("later", "sell", 101, 4)

        book.market_order("buyer", "buy", 5)

        self.assertEqual([(t.price, t.quantity, t.seller) for t in book.trades], [
            (100, 2, "better"), (101, 3, "first")
        ])
        self.assertNotIn(first, book.orders)
        self.assertEqual(book.depth("sell"), 4)

    def test_market_order_discards_unfilled_remainder(self):
        book = OrderBook()
        book.limit_order("maker", "sell", 101, 3)
        _, unfilled = book.market_order("taker", "buy", 5)
        self.assertEqual(unfilled, 2)
        self.assertEqual(book.depth("sell"), 0)
        self.assertEqual(book.account("taker").inventory, 3)

    def test_marketable_limit_rests_remainder_at_limit(self):
        book = OrderBook()
        book.limit_order("maker", "sell", 101, 2)
        order_id, remaining = book.limit_order("buyer", "buy", 102, 5)
        self.assertEqual(remaining, 3)
        self.assertEqual(book.best_bid, 102)
        self.assertEqual(book.orders[order_id].remaining, 3)
        self.assertEqual(book.account("buyer").inventory, 2)

    def test_cancel_removes_remainder_and_updates_depth(self):
        book = OrderBook()
        order_id, _ = book.limit_order("maker", "buy", 99, 7)
        self.assertEqual(book.depth("buy"), 7)
        self.assertEqual(book.cancel(order_id), 7)
        self.assertEqual(book.cancel(order_id), 0)
        self.assertIsNone(book.best_bid)

    def test_spread_depth_and_snapshot(self):
        book = OrderBook()
        book.limit_order("bid", "buy", 99, 5)
        book.limit_order("ask", "sell", 103, 8)
        book.limit_order("ask2", "sell", 104, 2)
        self.assertEqual(book.spread, 4)
        self.assertEqual(book.midpoint, 101)
        self.assertEqual(book.depth("sell", levels=1), 8)
        self.assertEqual(book.snapshot(1)["asks"], [(103, 8)])

    def test_average_cost_pnl_when_closing_and_flipping(self):
        book = OrderBook()
        for price, qty, side, trader in ((100, 10, "buy", "t"), (110, 4, "sell", "t"), (90, 10, "sell", "t")):
            counter = "counter"
            if side == "buy":
                book.limit_order(counter, "sell", price, qty)
            else:
                book.limit_order(counter, "buy", price, qty)
            book.market_order(trader, side, qty)
        account = book.account("t")
        self.assertEqual(account.inventory, -4)
        self.assertAlmostEqual(account.realised_pnl, -20)
        self.assertAlmostEqual(account.unrealised_pnl(80), 40)
        self.assertAlmostEqual(account.equity_pnl(80), 20)

    def test_input_validation(self):
        book = OrderBook()
        with self.assertRaises(ValueError):
            book.market_order("t", "hold", 1)
        with self.assertRaises(ValueError):
            book.limit_order("t", "buy", 0, 1)
        with self.assertRaises(ValueError):
            book.limit_order("t", "buy", 10, 0)


if __name__ == "__main__":
    unittest.main()
