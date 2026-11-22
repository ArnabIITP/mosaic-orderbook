"""Event-driven passive-vs-marketable policy study on explicit LOBSTER events.

The study uses a fixed top-five depth-imbalance signal, one outstanding order,
an explicit FIFO shadow queue, and a single exit attempt at a fixed horizon.
It is a one-day exploratory backtest, not a strategy claim.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from queue_execution import PassiveOrder, QueueEvent, market_sweep

ROOT = Path(__file__).resolve().parent
DATA_DIRS = (ROOT / "data" / "lobster_amzn_2012-06-21",
             ROOT / "data" / "lobster_other_tickers_2012-06-21")
TICK = 10_000
ORDER_SIZE = 100
DECISION_STRIDE = 1_000
IMBALANCE_THRESHOLD = 0.25
WORKING_MS = 250.0
HOLD_MS = 1_000.0
MAKER_FEE_BPS = 0.1
TAKER_FEE_BPS = 1.0


def _levels(raw: list[int]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    asks, bids = [], []
    for i in range(0, 20, 4):
        ap, aq, bp, bq = raw[i:i + 4]
        if 0 < ap < 9_000_000_000 and aq > 0:
            asks.append((ap, aq))
        if 0 < bp < 9_000_000_000 and bq > 0:
            bids.append((bp, bq))
    asks.sort()
    bids.sort(reverse=True)
    return asks, bids


def _imbalance(asks: list[tuple[int, int]], bids: list[tuple[int, int]]) -> float | None:
    total = sum(q for _, q in asks) + sum(q for _, q in bids)
    if not total:
        return None
    return (sum(q for _, q in bids) - sum(q for _, q in asks)) / total


@dataclass
class PolicyState:
    name: str
    inventory: int = 0
    cash: float = 0.0
    average_cost: float = 0.0
    realized: float = 0.0
    fees: float = 0.0
    turnover: float = 0.0
    max_abs_inventory: int = 0
    orders: int = 0
    entry_filled_orders: int = 0
    requested_entry_shares: int = 0
    filled_entry_shares: int = 0
    exit_shares: int = 0
    complete_entries: int = 0
    incomplete_exits: int = 0
    crossing_cancels: int = 0
    end_session_cancelled_shares: int = 0
    phase: str = "idle"
    submitted_time: float = 0.0
    target_time: float = 0.0
    side: str = "buy"
    limit_price: int = 0
    pending: PassiveOrder | None = None
    queue_key: tuple[str, int] = ("initial-ahead", 0)
    adds_after_submit: set[int] = field(default_factory=set)
    entry_qty: int = 0
    equity_peak: float = 0.0
    max_drawdown: float = 0.0
    equity_path: list[float] = field(default_factory=list)
    trades: int = 0
    last_activity_index: int = -1

    def _apply_fill(self, side: str, price_ticks: float, quantity: int, fee_bps: float) -> None:
        if quantity <= 0:
            return
        price = price_ticks / TICK
        signed = quantity if side == "buy" else -quantity
        old = self.inventory
        new = old + signed
        self.cash -= signed * price
        self.turnover += quantity * price
        fee = quantity * price * fee_bps / 10_000.0
        self.cash -= fee
        self.fees += fee
        if old == 0 or old * signed > 0:
            self.average_cost = (abs(old) * self.average_cost + quantity * price) / abs(new)
        else:
            closed = min(abs(old), quantity)
            self.realized += closed * (price - self.average_cost) * (1 if old > 0 else -1)
            if new == 0:
                self.average_cost = 0.0
            elif old * new < 0:
                self.average_cost = price
        self.inventory = new
        self.max_abs_inventory = max(self.max_abs_inventory, abs(new))

    def _mark(self, asks: list[tuple[int, int]], bids: list[tuple[int, int]]) -> None:
        if not asks or not bids:
            return
        mid = (asks[0][0] + bids[0][0]) / (2 * TICK)
        equity = self.cash + self.inventory * mid
        self.equity_peak = max(self.equity_peak, equity)
        self.max_drawdown = max(self.max_drawdown, self.equity_peak - equity)
        self.equity_path.append(equity)

    def _close_at_book(self, asks: list[tuple[int, int]], bids: list[tuple[int, int]], timestamp: float) -> None:
        if self.pending:
            self.pending.cancel_remainder()
        if self.entry_qty:
            exit_side = "sell" if self.side == "buy" else "buy"
            exit_levels = bids if exit_side == "sell" else asks
            result = market_sweep(exit_levels, self.entry_qty)
            exit_qty = int(result["filled_quantity"])
            self._apply_fill(exit_side, float(result["vwap"]) if exit_qty else 0,
                             exit_qty, TAKER_FEE_BPS)
            self.exit_shares += exit_qty
            if exit_qty == self.entry_qty:
                self.complete_entries += 1
            else:
                self.incomplete_exits += 1
        self.pending = None
        self.entry_qty = 0
        self.phase = "idle"
        self.last_activity_index = -1

    def on_event(self, index: int, timestamp: float, kind: int, order_id: int,
                 size: int, price: int, direction: int,
                 asks: list[tuple[int, int]], bids: list[tuple[int, int]],
                 shared_signal: float | None, requested: int,
                 working_ms: float, hold_ms: float) -> None:
        just_closed = False
        if self.phase == "entry" and self.pending:
            resting_dir = 1 if self.side == "buy" else -1
            if (kind in (2, 3, 4) and direction == resting_dir and price == self.limit_price
                    and order_id not in self.adds_after_submit and size > 0):
                event_kind = "trade" if kind == 4 else "cancel"
                self.pending.process(QueueEvent(int(timestamp * 1e9), event_kind,
                    self.side, self.limit_price, size, self.queue_key))
                newly_filled = self.pending.filled_quantity - self.entry_qty
                if newly_filled > 0:
                    self._apply_fill(self.side, self.limit_price, newly_filled, MAKER_FEE_BPS)
                    self.entry_qty += newly_filled
                    self.filled_entry_shares += newly_filled
                    if self.entry_qty == newly_filled:
                        self.entry_filled_orders += 1
            if kind == 1:
                self.adds_after_submit.add(order_id)
            crossed = (bool(asks) and asks[0][0] <= self.limit_price) if self.side == "buy" else (
                bool(bids) and bids[0][0] >= self.limit_price)
            if crossed and self.pending.remaining:
                self.pending.cancel_remainder()
                self.crossing_cancels += 1
                if self.pending.filled_quantity:
                    self.phase = "holding"
                else:
                    self.pending = None
                    self.phase = "idle"
                self.last_activity_index = index
            if self.phase == "entry" and timestamp - self.submitted_time >= working_ms / 1000.0:
                self.pending.cancel_remainder()
                if self.pending.filled_quantity:
                    self.phase = "holding"
                else:
                    self.pending = None
                    self.phase = "idle"
                    just_closed = True
                self.last_activity_index = index
        elif self.phase == "holding" and timestamp >= self.target_time:
            self._close_at_book(asks, bids, timestamp)
            just_closed = True

        if (self.phase == "idle" and self.inventory == 0 and not just_closed
                and shared_signal is not None and asks and bids):
                self.side = "buy" if shared_signal > 0 else "sell"
                self.submitted_time = timestamp
                self.target_time = timestamp + hold_ms / 1000.0
                self.orders += 1
                self.requested_entry_shares += requested
                if self.name == "marketable":
                    levels = asks if self.side == "buy" else bids
                    entry = market_sweep(levels, requested)
                    filled = int(entry["filled_quantity"])
                    self.entry_qty = filled
                    if filled:
                        px = float(entry["vwap"])
                        self._apply_fill(self.side, px, filled, TAKER_FEE_BPS)
                        self.filled_entry_shares += filled
                        self.entry_filled_orders += 1
                        self.phase = "holding"
                    else:
                        self.phase = "idle"
                else:
                    self.limit_price = bids[0][0] if self.side == "buy" else asks[0][0]
                    queue_size = bids[0][1] if self.side == "buy" else asks[0][1]
                    self.queue_key = ("initial-ahead", index)
                    ahead = [(self.queue_key, queue_size)] if queue_size else []
                    self.pending = PassiveOrder(order_id=(self.name, index), side=self.side,
                        price=self.limit_price, quantity=requested,
                        submitted_ns=int(timestamp * 1e9), ahead_orders=ahead)
                    self.adds_after_submit = set()
                    self.phase = "entry"
                self.trades += 1
                self.last_activity_index = index
        self._mark(asks, bids)

    def summary(self, final_mid: float | None) -> dict[str, Any]:
        unrealized = self.inventory * (final_mid - self.average_cost) if final_mid is not None else None
        marked = self.realized - self.fees + unrealized if unrealized is not None else None
        return {
            "policy": self.name,
            "orders": self.orders,
            "entry_orders_with_any_fill": self.entry_filled_orders,
            "entry_order_fill_probability_pct": 100 * self.entry_filled_orders / self.orders if self.orders else 0.0,
            "requested_entry_shares": self.requested_entry_shares,
            "filled_entry_shares": self.filled_entry_shares,
            "entry_fill_fraction_pct": 100 * self.filled_entry_shares / self.requested_entry_shares if self.requested_entry_shares else 0.0,
            "completed_entries": self.complete_entries,
            "incomplete_exits": self.incomplete_exits,
            "crossing_cancellations": self.crossing_cancels,
            "end_session_cancelled_shares": self.end_session_cancelled_shares,
            "exit_shares": self.exit_shares,
            "max_abs_inventory_shares": self.max_abs_inventory,
            "final_inventory_shares": self.inventory,
            "gross_realized_pnl_usd": self.realized,
            "fees_usd": self.fees,
            "net_realized_pnl_usd": self.realized - self.fees,
            "final_unrealized_pnl_usd": unrealized,
            "final_marked_pnl_usd": marked,
            "turnover_usd": self.turnover,
            "net_realized_pnl_bps_per_turnover": (self.realized - self.fees) / self.turnover * 10_000 if self.turnover else None,
            "final_marked_pnl_bps_per_turnover": marked / self.turnover * 10_000 if self.turnover and marked is not None else None,
            "max_marked_equity_drawdown_usd": self.max_drawdown,
        }


def _analyze_file(message_file: Path, requested: int, decision_stride: int,
                  threshold: float, working_ms: float, hold_ms: float) -> dict[str, Any]:
    book_file = message_file.with_name(message_file.name.replace("_message_", "_orderbook_"))
    policies = [PolicyState("passive_fifo"), PolicyState("marketable")]
    source_rows = 0
    last_mid: float | None = None
    fills_by_type = {str(kind): 0 for kind in range(1, 8)}
    with message_file.open(newline="", encoding="utf-8") as mf, book_file.open(newline="", encoding="utf-8") as bf:
        for index, (msg, raw_book) in enumerate(zip(csv.reader(mf), csv.reader(bf), strict=True)):
            source_rows += 1
            timestamp = float(msg[0])
            kind, order_id, size, price, direction = int(msg[1]), int(msg[2]), int(msg[3]), int(msg[4]), int(msg[5])
            if str(kind) in fills_by_type:
                fills_by_type[str(kind)] += 1
            asks, bids = _levels([int(value) for value in raw_book])
            if asks and bids and asks[0][0] > bids[0][0]:
                last_mid = (asks[0][0] + bids[0][0]) / (2 * TICK)
            shared_signal = None
            if (index % decision_stride == 0 and all(p.phase == "idle" and p.inventory == 0
                    and index > p.last_activity_index for p in policies)):
                candidate = _imbalance(asks, bids) if asks and bids else None
                if candidate is not None and abs(candidate) >= threshold:
                    shared_signal = candidate
            for policy in policies:
                policy.on_event(index, timestamp, kind, order_id, size, price, direction,
                    asks, bids, shared_signal, requested, working_ms, hold_ms)
    for policy in policies:
        if policy.pending:
            policy.end_session_cancelled_shares += policy.pending.cancel_remainder()
            policy.pending = None
            policy.phase = "idle"
    results = {policy.name: policy.summary(last_mid) for policy in policies}
    return {"ticker": message_file.name.split("_")[0], "date": "2012-06-21",
            "source_events": source_rows, "explicit_execution_events": fills_by_type["4"],
            "results": results}


def run(data_dirs: tuple[Path, ...], output_json: Path, output_report: Path,
        requested: int = ORDER_SIZE, decision_stride: int = DECISION_STRIDE,
        threshold: float = IMBALANCE_THRESHOLD, working_ms: float = WORKING_MS,
        hold_ms: float = HOLD_MS) -> dict[str, Any]:
    files = sorted(file for directory in data_dirs if directory.exists()
                   for file in directory.glob("*_message_5.csv"))
    if not files:
        raise FileNotFoundError("No LOBSTER message files found. See data/README.md.")
    if min(requested, decision_stride) <= 0 or not 0 <= threshold <= 1 or min(working_ms, hold_ms) <= 0:
        raise ValueError("size and stride must be positive; threshold must be in [0, 1]; durations must be positive")
    per_ticker = [_analyze_file(path, requested, decision_stride, threshold, working_ms, hold_ms)
                  for path in files]
    policy_names = ("passive_fifo", "marketable")
    pooled: dict[str, Any] = {}
    for name in policy_names:
        rows = [entry["results"][name] for entry in per_ticker]
        orders = sum(row["orders"] for row in rows)
        req = sum(row["requested_entry_shares"] for row in rows)
        filled = sum(row["filled_entry_shares"] for row in rows)
        pooled[name] = {
            "policy": name, "orders": orders,
            "entry_orders_with_any_fill": sum(r["entry_orders_with_any_fill"] for r in rows),
            "entry_order_fill_probability_pct": 100 * sum(r["entry_orders_with_any_fill"] for r in rows) / orders if orders else 0.0,
            "requested_entry_shares": req, "filled_entry_shares": filled,
            "entry_fill_fraction_pct": 100 * filled / req if req else 0.0,
            "completed_entries": sum(r["completed_entries"] for r in rows),
            "incomplete_exits": sum(r["incomplete_exits"] for r in rows),
            "crossing_cancellations": sum(r["crossing_cancellations"] for r in rows),
            "end_session_cancelled_shares": sum(r["end_session_cancelled_shares"] for r in rows),
            "exit_shares": sum(r["exit_shares"] for r in rows),
            "max_abs_inventory_shares": max((r["max_abs_inventory_shares"] for r in rows), default=0),
            "final_inventory_shares": sum(r["final_inventory_shares"] for r in rows),
            "gross_realized_pnl_usd": sum(r["gross_realized_pnl_usd"] for r in rows),
            "fees_usd": sum(r["fees_usd"] for r in rows),
            "net_realized_pnl_usd": sum(r["net_realized_pnl_usd"] for r in rows),
            "final_unrealized_pnl_usd": sum(r["final_unrealized_pnl_usd"] or 0 for r in rows),
            "final_marked_pnl_usd": sum(r["final_marked_pnl_usd"] or 0 for r in rows),
            "turnover_usd": sum(r["turnover_usd"] for r in rows),
            "net_realized_pnl_bps_per_turnover": (sum(r["net_realized_pnl_usd"] for r in rows) /
                sum(r["turnover_usd"] for r in rows) * 10_000 if sum(r["turnover_usd"] for r in rows) else None),
            "final_marked_pnl_bps_per_turnover": (sum(r["final_marked_pnl_usd"] or 0 for r in rows) /
                sum(r["turnover_usd"] for r in rows) * 10_000 if sum(r["turnover_usd"] for r in rows) else None),
            "sum_of_per_ticker_max_drawdowns_usd": sum(r["max_marked_equity_drawdown_usd"] for r in rows),
        }
    result = {
        "study": "Explicit-message LOBSTER event-driven execution policy comparison",
        "sample": "Five US stocks, one session: 2012-06-21",
        "policy": {"signal": "Top-five displayed-depth imbalance; buy at >= threshold, sell at <= -threshold.",
                   "requested_shares": requested, "decision_stride_events": decision_stride,
                   "imbalance_threshold": threshold, "passive_working_time_ms": working_ms,
                   "holding_horizon_ms": hold_ms, "fees_bps_per_side": {"maker": MAKER_FEE_BPS, "taker": TAKER_FEE_BPS},
                   "inventory_limit_shares": requested, "position_overlap": "One working order or open position per policy per ticker; no overlapping entries."},
        "event_semantics": {"visible_execution": "LOBSTER message type 4 consumes FIFO queue ahead and then the shadow order.",
                            "cancellation": "Types 2 and 3 remove only initial displayed queue; no fills.",
                            "hidden_execution": "Type 5 is not treated as visible queue depletion.",
                            "adds_after_entry": "Type 1 adds are behind the simulated order.",
                            "crossing": "Cancel remaining post-only quantity if the opposite best quote touches/crosses the limit; do not infer a fill from counterfactual interaction."},
        "per_ticker": per_ticker, "pooled": pooled,
        "limitations": ["The LOBSTER sample covers one day and five stocks; this is exploratory, not out-of-period evidence.",
                        "The initial queue is represented as aggregate displayed size at the best quote because prior order IDs are not available before the sample begins.",
                        "Marketable entries and exits use the available five displayed levels; deeper residual quantity remains inventory and is marked at the final midpoint.",
                        "The strategy threshold and fee assumptions are illustrative, and no parameter search or portfolio capital model is performed."],
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report = ["# Explicit-Execution LOBSTER Policy Backtest", "",
        "## Setup", "",
        f"A fixed top-five depth-imbalance rule is replayed on {len(per_ticker)} LOBSTER stocks for one session (2012-06-21). Both policies receive the same non-overlapping signal intents: the next intent is generated only when both are flat, sampled every {decision_stride:,} events. The requested size is {requested} shares; passive orders queue at the best quote for {working_ms:g} ms, then any filled quantity is held to {hold_ms:g} ms and exited against the contemporaneous displayed book. The marketable baseline sweeps at entry and uses the same exit rule. Fees are assumed, not sourced from exchange fee schedules.", "",
        "Visible type-4 execution messages reduce queue ahead and then fill the passive order; type-2/3 cancellations reduce queue ahead without a fill; type-1 additions after submission are behind; hidden type-5 executions are not treated as visible queue depletion. The initial ahead queue is aggregate displayed size because order IDs preceding the sample are unavailable.", "",
        "## Pooled results", "",
        "| Policy | Orders | Any-fill | Entry fill fraction | Completed entries | Incomplete exits | End-session canceled shares | Net realized P&L | Fees | Final inventory | Final marked P&L | Marked bps / turnover | Sum of per-stock max drawdowns | Turnover |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in pooled.items():
        label = "Passive FIFO" if name == "passive_fifo" else "Marketable baseline"
        drawdown = row.get("sum_of_per_ticker_max_drawdowns_usd", row.get("max_marked_equity_drawdown_usd", 0.0))
        marked_bps = row.get("final_marked_pnl_bps_per_turnover")
        report.append(f"| {label} | {row['orders']:,} | {row['entry_order_fill_probability_pct']:.1f}% | {row['entry_fill_fraction_pct']:.1f}% | {row['completed_entries']:,} | {row['incomplete_exits']:,} | {row['end_session_cancelled_shares']:,} | ${row['net_realized_pnl_usd']:.2f} | ${row['fees_usd']:.2f} | {row['final_inventory_shares']:,} | ${row['final_marked_pnl_usd']:.2f} | {f'{marked_bps:.2f}' if marked_bps is not None else 'n/a'} | ${drawdown:.2f} | ${row['turnover_usd']:,.0f} |")
    report.extend(["", "## Per-stock results", "", "| Ticker | Policy | Orders | Any-fill | Fill fraction | End-session canceled shares | Net realized P&L | Fees | Final inventory | Final marked P&L | Marked bps / turnover | Max drawdown |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for ticker in per_ticker:
        for name, row in ticker["results"].items():
            label = "Passive FIFO" if name == "passive_fifo" else "Marketable baseline"
            marked_bps = row.get("final_marked_pnl_bps_per_turnover")
            report.append(f"| {ticker['ticker']} | {label} | {row['orders']:,} | {row['entry_order_fill_probability_pct']:.1f}% | {row['entry_fill_fraction_pct']:.1f}% | {row['end_session_cancelled_shares']:,} | ${row['net_realized_pnl_usd']:.2f} | ${row['fees_usd']:.2f} | {row['final_inventory_shares']:,} | ${row['final_marked_pnl_usd']:.2f} | {f'{marked_bps:.2f}' if marked_bps is not None else 'n/a'} | ${row['max_marked_equity_drawdown_usd']:.2f} |")
    report.extend(["", "## Interpretation and limits", "",
        "This is the first event-driven check with explicit visible executions, partial fills, cancellations, inventory accounting, fees, and a marketable comparator. It does not validate the WSELOB queue estimates: it uses a different venue, five-level sample, one historical day, and a hand-specified imbalance rule. Orders are non-overlapping and paired across policies within each ticker; no statistical significance is claimed from a single day. Dollar P&L is also shown in bps per round-trip turnover to make the different share prices more comparable.", "",
        "The simulator has no market impact or replenishment model. Post-only orders that would become marketable are canceled conservatively. If exit depth is insufficient, residual inventory remains and is marked at the final midpoint. Fees are illustrative. See `data/README.md` for data terms and source details.", "",
        "Reproduce with `python lobster_queue_backtest.py`.", ""])
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text("\n".join(report), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, action="append", dest="data_dirs")
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "lobster_queue_backtest.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "lobster_queue_backtest.md")
    parser.add_argument("--size", type=int, default=ORDER_SIZE)
    parser.add_argument("--decision-stride", type=int, default=DECISION_STRIDE)
    parser.add_argument("--imbalance-threshold", type=float, default=IMBALANCE_THRESHOLD)
    parser.add_argument("--working-ms", type=float, default=WORKING_MS)
    parser.add_argument("--hold-ms", type=float, default=HOLD_MS)
    args = parser.parse_args()
    result = run(tuple(args.data_dirs or DATA_DIRS), args.output_json, args.output_report,
                 args.size, args.decision_stride, args.imbalance_threshold,
                 args.working_ms, args.hold_ms)
    print(json.dumps({"per_ticker": result["per_ticker"], "pooled": result["pooled"],
                      "outputs": [str(args.output_json), str(args.output_report)]}, indent=2))


if __name__ == "__main__":
    main()
