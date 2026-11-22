"""Run a tiny event-driven queue example without downloading market data."""
from __future__ import annotations

import json

from queue_execution import PassiveOrder, QueueEvent, market_sweep


def run_demo() -> dict[str, object]:
    order = PassiveOrder(
        order_id=9001,
        side="buy",
        price=100,
        quantity=500,
        submitted_ns=0,
        ahead_orders=[(101, 300), (102, 250)],
    )
    events = [
        QueueEvent(1, "trade", "buy", 100, 200),
        QueueEvent(2, "cancel", "buy", 100, 200, order_id=102),
        QueueEvent(3, "trade", "buy", 100, 250),
        QueueEvent(4, "add", "buy", 100, 500, order_id=103),
        QueueEvent(5, "trade", "buy", 100, 200),
    ]
    timeline = []
    for event in events:
        filled_now = order.process(event)
        timeline.append({"event": event.kind, "quantity": event.quantity,
                         "queue_ahead": order.queue_ahead,
                         "filled_now": filled_now,
                         "filled_total": order.filled_quantity})
    unfilled_cancelled = order.cancel_remainder()
    market = market_sweep([(101, 300), (102, 400)], 500)
    return {
        "description": "Synthetic demonstration only; it is not a historical trading result.",
        "passive_buy": {
            "limit_price": order.price,
            "requested_quantity": order.quantity,
            "timeline": timeline,
            "cancelled_remainder_at_horizon": unfilled_cancelled,
            "markout_at_terminal_mid_101_2": order.midpoint_markout(101.2, fee_bps_per_side=0.1),
        },
        "marketable_buy_baseline": market,
        "marketable_buy_vwap": market["vwap"],
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
