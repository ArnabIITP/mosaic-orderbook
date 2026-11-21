#include "order_book.hpp"

#include <cassert>

int main() {
    lob::OrderBook book;
    const auto ask1 = book.restore_limit_order("maker1", lob::Side::Sell, 101, 10);
    const auto ask2 = book.restore_limit_order("maker2", lob::Side::Sell, 101, 7);
    assert(book.modify_remaining(ask1, 4));
    auto asks = book.snapshot(lob::Side::Sell, 2);
    assert(asks.size() == 1 && asks[0].price == 101 && asks[0].quantity == 11);
    assert(book.cancel(ask1) == 4);
    assert(book.cancel(ask2) == 7);
    assert(book.snapshot(lob::Side::Sell).empty());

    book.limit_order("seller", lob::Side::Sell, 102, 5);
    book.limit_order("buyer", lob::Side::Buy, 99, 3);
    const auto fill = book.market_order("taker", lob::Side::Buy, 8);
    assert(fill.unfilled == 3);
    assert(book.trades().size() == 1 && book.trades()[0].quantity == 5);
    assert(book.depth(lob::Side::Sell) == 0);
    assert(book.accounts().at("taker").inventory == 5);

    book.clear();
    assert(book.snapshot(lob::Side::Buy).empty());
    assert(book.snapshot(lob::Side::Sell).empty());
    assert(book.trades().empty());
    return 0;
}
