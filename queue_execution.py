"""Queue-aware passive limit-order fills on an explicit event stream.

This module separates trade prints from cancellations. Unknown venue messages must
be mapped conservatively before they are replayed through this simulator.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Hashable, Literal, Sequence

Side = Literal["buy", "sell"]
EventKind = Literal["trade", "cancel", "add"]


@dataclass(frozen=True)
class QueueEvent:
    timestamp_ns: int
    kind: EventKind
    resting_side: Side
    price: int
    quantity: int
    order_id: Hashable | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ns < 0 or self.price <= 0 or self.quantity <= 0:
            raise ValueError("event timestamp, price, and quantity must be positive (timestamp may be zero)")
        if self.kind in ("cancel", "add") and self.order_id is None:
            raise ValueError(f"{self.kind} events require an order_id")


@dataclass(frozen=True)
class Fill:
    timestamp_ns: int
    price: int
    quantity: int


class PassiveOrder:
    """One shadow passive order behind the visible FIFO queue at its price."""

    def __init__(
        self,
        *,
        order_id: Hashable,
        side: Side,
        price: int,
        quantity: int,
        submitted_ns: int,
        ahead_orders: Sequence[tuple[Hashable, int]],
    ) -> None:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if price <= 0 or quantity <= 0 or submitted_ns < 0:
            raise ValueError("price and quantity must be positive; timestamp must be non-negative")
        ahead: OrderedDict[Hashable, int] = OrderedDict()
        for external_id, external_qty in ahead_orders:
            if external_qty <= 0 or external_id in ahead:
                raise ValueError("ahead orders must have unique ids and positive quantities")
            ahead[external_id] = external_qty
        self.order_id = order_id
        self.side = side
        self.price = price
        self.quantity = quantity
        self.remaining = quantity
        self.submitted_ns = submitted_ns
        self.ahead = ahead
        self.fills: list[Fill] = []
        self.cancelled_quantity = 0
        self.ignored_events = 0

    @property
    def queue_ahead(self) -> int:
        return sum(self.ahead.values())

    @property
    def filled_quantity(self) -> int:
        return sum(fill.quantity for fill in self.fills)

    @property
    def fill_rate(self) -> float:
        return self.filled_quantity / self.quantity

    @property
    def fill_vwap(self) -> float | None:
        if not self.fills:
            return None
        return sum(fill.price * fill.quantity for fill in self.fills) / self.filled_quantity

    def process(self, event: QueueEvent) -> int:
        """Apply one market event; return newly filled quantity.

        Trades consume FIFO quantity ahead and then fill this order. Cancels only
        remove the named ahead order and never create a fill. Adds occur behind
        this order and therefore do not alter its priority.
        """
        if self.remaining == 0:
            return 0
        if event.timestamp_ns < self.submitted_ns:
            raise ValueError("cannot process an event before the simulated order was submitted")
        if event.resting_side != self.side or event.price != self.price:
            self.ignored_events += 1
            return 0
        if event.kind == "add":
            return 0
        if event.kind == "cancel":
            if event.order_id in self.ahead:
                left = self.ahead[event.order_id] - event.quantity
                if left > 0:
                    self.ahead[event.order_id] = left
                else:
                    del self.ahead[event.order_id]
            return 0

        traded = event.quantity
        while traded and self.ahead:
            first_id = next(iter(self.ahead))
            take = min(traded, self.ahead[first_id])
            traded -= take
            ahead_left = self.ahead[first_id] - take
            if ahead_left:
                self.ahead[first_id] = ahead_left
            else:
                del self.ahead[first_id]
        newly_filled = min(traded, self.remaining)
        if newly_filled:
            self.remaining -= newly_filled
            self.fills.append(Fill(event.timestamp_ns, self.price, newly_filled))
        return newly_filled

    def cancel_remainder(self) -> int:
        """Cancel the simulated order remainder at the end of its observation window."""
        cancelled = self.remaining
        self.cancelled_quantity += cancelled
        self.remaining = 0
        return cancelled

    def midpoint_markout(self, terminal_mid: float, fee_bps_per_side: float = 0.0) -> dict[str, float | int | None]:
        """Mark filled shares to a terminal midpoint, less the assumed maker fee.

        This is a midpoint markout diagnostic, not realized exit P&L. A separate
        executable exit model is required to account for spread and impact.
        """
        if terminal_mid <= 0 or fee_bps_per_side < 0:
            raise ValueError("terminal_mid must be positive and fee cannot be negative")
        filled = self.filled_quantity
        if not filled:
            return {"filled_quantity": 0, "fill_rate": 0.0, "gross_markout_bps": None,
                    "maker_fee_bps": fee_bps_per_side, "net_markout_bps": None}
        avg = float(self.fill_vwap)
        direction = 1.0 if self.side == "buy" else -1.0
        gross = direction * (terminal_mid - avg) / avg * 10_000.0
        return {"filled_quantity": filled, "fill_rate": self.fill_rate, "gross_markout_bps": gross,
                "maker_fee_bps": fee_bps_per_side, "net_markout_bps": gross - fee_bps_per_side}


def market_sweep(levels: Sequence[tuple[int, int]], quantity: int) -> dict[str, float | int | None]:
    """Return fill quantity and VWAP for an ask- or bid-side price ladder."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    remaining, filled, notional = quantity, 0, 0
    for price, available in levels:
        if price <= 0 or available < 0:
            raise ValueError("levels require positive prices and non-negative quantities")
        take = min(remaining, available)
        notional += take * price
        filled += take
        remaining -= take
        if remaining == 0:
            break
    return {"requested_quantity": quantity, "filled_quantity": filled,
            "fill_rate": filled / quantity, "vwap": notional / filled if filled else None,
            "unfilled_quantity": remaining}
