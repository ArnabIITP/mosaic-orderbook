"""A small, single-instrument price-time-priority limit order book.

Prices are integer ticks and quantities are positive integers. The matching
engine is intentionally simple and uses Python lists; it is a useful reference
implementation before optimizing the hot path in C++.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import count
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass
class Order:
    order_id: int
    trader_id: str
    side: Side
    price: int
    quantity: int
    remaining: int
    timestamp: int


@dataclass(frozen=True)
class Trade:
    price: int
    quantity: int
    buyer: str
    seller: str
    buy_order_id: int
    sell_order_id: int


@dataclass
class Account:
    inventory: int = 0
    cash: float = 0.0
    realised_pnl: float = 0.0
    average_cost: float = 0.0

    def apply_fill(self, side: Side, price: int, quantity: int) -> None:
        """Update cash, inventory, and average-cost realised P&L."""
        signed_qty = quantity if side == "buy" else -quantity
        old = self.inventory
        new = old + signed_qty
        self.cash -= signed_qty * price

        if old == 0 or old * signed_qty > 0:
            self.average_cost = (
                (abs(old) * self.average_cost + quantity * price) / abs(new)
            )
        else:
            closed = min(abs(old), quantity)
            self.realised_pnl += closed * (price - self.average_cost) * (1 if old > 0 else -1)
            if new == 0:
                self.average_cost = 0.0
            elif old * new < 0:
                self.average_cost = float(price)
        self.inventory = new

    def unrealised_pnl(self, mark: float) -> float:
        return self.inventory * (mark - self.average_cost)

    def equity_pnl(self, mark: float) -> float:
        return self.realised_pnl + self.unrealised_pnl(mark)


class OrderBook:
    """Single-instrument order book with FIFO priority at each price."""

    def __init__(self) -> None:
        self.bids: dict[int, list[Order]] = defaultdict(list)
        self.asks: dict[int, list[Order]] = defaultdict(list)
        self.orders: dict[int, Order] = {}
        self.accounts: dict[str, Account] = defaultdict(Account)
        self.trades: list[Trade] = []
        self._ids = count(1)
        self._time = count(1)

    @property
    def best_bid(self) -> int | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> int | None:
        return min(self.asks) if self.asks else None

    @property
    def spread(self) -> int | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def midpoint(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    def depth(self, side: Side, levels: int | None = None) -> int:
        book = self.bids if side == "buy" else self.asks
        prices = sorted(book, reverse=(side == "buy"))
        if levels is not None:
            if levels < 0:
                raise ValueError("levels must be non-negative")
            prices = prices[:levels]
        return sum(order.remaining for p in prices for order in book[p])

    def _submit(self, trader_id: str, side: Side, quantity: int, price: int | None) -> tuple[int, int]:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if price is not None and price <= 0:
            raise ValueError("price must be positive")

        order_id = next(self._ids)
        incoming = Order(order_id, trader_id, side, price or 0, quantity, quantity, next(self._time))
        resting = self.asks if side == "buy" else self.bids

        while incoming.remaining and resting:
            best = min(resting) if side == "buy" else max(resting)
            crosses = price is None or (best <= price if side == "buy" else best >= price)
            if not crosses:
                break
            queue = resting[best]
            resting_order = queue[0]
            filled = min(incoming.remaining, resting_order.remaining)
            incoming.remaining -= filled
            resting_order.remaining -= filled
            if side == "buy":
                buy_id, sell_id = incoming.order_id, resting_order.order_id
                buyer, seller = trader_id, resting_order.trader_id
            else:
                buy_id, sell_id = resting_order.order_id, incoming.order_id
                buyer, seller = resting_order.trader_id, trader_id
            self.accounts[buyer].apply_fill("buy", best, filled)
            self.accounts[seller].apply_fill("sell", best, filled)
            self.trades.append(Trade(best, filled, buyer, seller, buy_id, sell_id))
            if resting_order.remaining == 0:
                queue.pop(0)
                del self.orders[resting_order.order_id]
            if not queue:
                del resting[best]

        if incoming.remaining and price is not None:
            incoming.price = price
            own = self.bids if side == "buy" else self.asks
            own[price].append(incoming)
            self.orders[order_id] = incoming
        return order_id, incoming.remaining

    def limit_order(self, trader_id: str, side: Side, price: int, quantity: int) -> tuple[int, int]:
        """Submit a limit order; returns (order_id, unfilled_quantity)."""
        return self._submit(trader_id, side, quantity, price)

    def market_order(self, trader_id: str, side: Side, quantity: int) -> tuple[int, int]:
        """Submit a market order; any unfilled remainder is discarded."""
        return self._submit(trader_id, side, quantity, None)

    def cancel(self, order_id: int) -> int:
        """Cancel a live order and return its remaining quantity."""
        order = self.orders.pop(order_id, None)
        if order is None:
            return 0
        book = self.bids if order.side == "buy" else self.asks
        queue = book[order.price]
        queue.remove(order)
        if not queue:
            del book[order.price]
        return order.remaining

    def account(self, trader_id: str) -> Account:
        return self.accounts[trader_id]

    def mark_to_market(self, trader_id: str, mark: float | None = None) -> dict[str, float | int]:
        if mark is None:
            mark = self.midpoint
        if mark is None:
            raise ValueError("a mark is required while the book has no two-sided quote")
        account = self.accounts[trader_id]
        return {
            "inventory": account.inventory,
            "cash": account.cash,
            "realised_pnl": account.realised_pnl,
            "unrealised_pnl": account.unrealised_pnl(mark),
            "total_pnl": account.equity_pnl(mark),
        }

    def snapshot(self, levels: int = 5) -> dict[str, object]:
        if levels < 0:
            raise ValueError("levels must be non-negative")
        bid_prices = sorted(self.bids, reverse=True)[:levels]
        ask_prices = sorted(self.asks)[:levels]
        return {
            "bids": [(p, sum(o.remaining for o in self.bids[p])) for p in bid_prices],
            "asks": [(p, sum(o.remaining for o in self.asks[p])) for p in ask_prices],
            "spread": self.spread,
            "midpoint": self.midpoint,
        }
