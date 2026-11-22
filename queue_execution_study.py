"""Queue-position sensitivity study using WSELOB order-event updates."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pandas as pd

from analyze_wselob import _resting, _side
from queue_execution import PassiveOrder, QueueEvent, market_sweep

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "wselob_2017" / "PEKAO_lob_2017_zlib.h5"
DEFAULT_SIGNALS = ROOT / "results" / "execution_aware_predictions.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _key(row: Any) -> tuple[int, int]:
    return int(row["order_date"]), int(row["order_id"])


def _apply_book_row(orders: dict[tuple[int, int], tuple[int, int, int, int]], row: Any) -> None:
    """Maintain (side, price, quantity, priority_date) by source order identity."""
    action = bytes(row["action_type"]).strip()
    key = _key(row)
    if action == b"F":
        orders.clear()
        return
    if action == b"D":
        orders.pop(key, None)
        return
    if action == b"A":
        if _resting(row):
            orders[key] = (int(_side(row)), int(row["price"]), int(row["volume"]),
                           int(row["priority_date"]))
        return
    if action not in (b"M", b"Y"):
        return
    old = orders.get(key)
    if old is None:
        if _resting(row):
            orders[key] = (int(_side(row)), int(row["price"]), int(row["volume"]),
                           int(row["priority_date"]))
        return
    old_side, old_price, old_qty, old_priority = old
    row_side = _side(row)
    side = row_side if row_side is not None else old_side
    price = int(row["price"])
    price = old_price if price == -1 else price
    qty = int(row["volume"])
    qty = old_qty if qty == -1 else qty
    priority = int(row["priority_date"])
    priority = old_priority if priority == -1 else priority
    orders.pop(key, None)
    if price > 0 and qty > 0 and bytes(row["order_type"]).strip() in (b"2", b"P", b"K"):
        orders[key] = (side, price, qty, priority)


def _book_levels(orders: dict[tuple[int, int], tuple[int, int, int, int]]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    bids: dict[int, int] = defaultdict(int)
    asks: dict[int, int] = defaultdict(int)
    for side, price, quantity, _priority in orders.values():
        if quantity > 0 and price > 0:
            (bids if side == 1 else asks)[price] += quantity
    return (sorted(asks.items()), sorted(bids.items(), reverse=True))


def _capture_state(orders: dict[tuple[int, int], tuple[int, int, int, int]],
                   signal: dict[str, Any]) -> dict[str, Any] | None:
    side_name = "buy" if signal["prediction"] == "up" else "sell" if signal["prediction"] == "down" else None
    if side_name is None:
        return None
    side = 1 if side_name == "buy" else 2
    prices = [value[1] for value in orders.values() if value[0] == side and value[2] > 0]
    if not prices:
        return None
    limit_price = max(prices) if side == 1 else min(prices)
    queue = [
        (key, value[2], value[3], key[0])
        for key, value in orders.items()
        if value[0] == side and value[1] == limit_price and value[2] > 0
    ]
    queue.sort(key=lambda item: (item[2] if item[2] >= 0 else item[3], item[3], item[0]))
    timestamp = int(round(float(signal["timestamp"]) * 1e9))
    target_ns = int(round((float(signal["timestamp"]) + float(signal["target_delay_seconds"])) * 1e9))
    asks, bids = _book_levels(orders)
    return {"signal": signal, "side": side_name, "side_code": side,
            "price": limit_price, "ahead": [(order_key, quantity) for order_key, quantity, _, _ in queue],
            "submit_ns": timestamp, "target_ns": target_ns,
            "terminal_mid": float(signal["future_mid"]),
            "entry_asks": asks, "entry_bids": bids,
            "book_orders": [(key[0], key[1], value[0], value[1], value[2], value[3])
                            for key, value in orders.items()]}


def _capture_target_book(orders: dict[tuple[int, int], tuple[int, int, int, int]]) -> dict[str, Any] | None:
    asks, bids = _book_levels(orders)
    if not asks or not bids or asks[0][0] <= bids[0][0]:
        return None
    return {"asks": asks, "bids": bids, "mid": (asks[0][0] + bids[0][0]) / 2}


def _target_signal_states(data: h5py.File, signals: pd.DataFrame, event_stride: int) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    entries: dict[tuple[str, int], dict[str, Any]] = {}
    targets: dict[tuple[str, int], dict[str, Any]] = {}
    for date, rows in signals.groupby("date", sort=True):
        wanted: dict[int, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for _, row in rows.iterrows():
            signal = row.to_dict()
            wanted[int(signal["event_index"])].append(("entry", signal))
            wanted[int(signal["future_event_index"])].append(("target", signal))
        if not wanted:
            continue
        group = data[f"d{date}"]["table"]
        orders: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        group_time: int | None = None
        last_index = -1
        sample_requested = False

        def maybe_capture() -> None:
            if sample_requested and last_index in wanted:
                for kind, signal in wanted[last_index]:
                    if kind == "entry":
                        state = _capture_state(orders, signal)
                        if state is not None:
                            state["date"] = date
                            state["event_index"] = last_index
                            entries[(date, last_index)] = state
                    else:
                        book = _capture_target_book(orders)
                        if book is not None:
                            targets[(date, last_index)] = book

        for start in range(0, len(group), 50_000):
            block = group[start:start + 50_000]
            for offset, row in enumerate(block):
                i = start + offset
                timestamp = int(row["time"])
                if group_time is not None and timestamp != group_time:
                    maybe_capture()
                    sample_requested = False
                group_time = timestamp
                last_index = i
                _apply_book_row(orders, row)
                if i % event_stride == 0:
                    sample_requested = True
        maybe_capture()
    return entries, targets


def _simulate_window(dataset: h5py.Dataset, state: dict[str, Any], order_size: int,
                     interpretation: str, latency_ns: int) -> dict[str, Any]:
    orders = {(order_date, order_id): (side, price, quantity, priority)
              for order_date, order_id, side, price, quantity, priority in state["book_orders"]}
    start = state["event_index"] + 1
    target_ns = state["target_ns"]
    submit_ns = state["submit_ns"]
    if latency_ns:
        cutoff = submit_ns + latency_ns
        cursor = start
        while cursor < len(dataset):
            timestamp = int(dataset[cursor]["time"])
            if timestamp >= cutoff:
                # Placement occurs after the full arrival timestamp group.
                while cursor < len(dataset) and int(dataset[cursor]["time"]) == timestamp:
                    _apply_book_row(orders, dataset[cursor])
                    cursor += 1
                submit_ns = timestamp
                break
            _apply_book_row(orders, dataset[cursor])
            cursor += 1
        if cursor >= len(dataset) or submit_ns >= target_ns:
            return {"order": None, "stopped_by_refresh": False,
                    "events_processed": cursor - start, "arrival_after_target": True}
        prices = [row[1] for row in orders.values() if row[0] == state["side_code"] and row[2] > 0]
        if not prices:
            return {"order": None, "stopped_by_refresh": False,
                    "events_processed": cursor - start, "arrival_after_target": False}
        limit_price = max(prices) if state["side_code"] == 1 else min(prices)
        queue = [(key, row[2], row[3], key[0]) for key, row in orders.items()
                 if row[0] == state["side_code"] and row[1] == limit_price and row[2] > 0]
        queue.sort(key=lambda item: (item[2] if item[2] >= 0 else item[3], item[3], item[0][1]))
        ahead = [(order_key, quantity) for order_key, quantity, _, _ in queue]
        price = limit_price
        start = cursor
    else:
        price, ahead = state["price"], state["ahead"]

    passive = PassiveOrder(order_id=-1, side=state["side"], price=price,
                           quantity=order_size, submitted_ns=submit_ns,
                           ahead_orders=ahead)
    live_orders = dict(orders)
    stopped_by_refresh = False
    for start_at in range(start, len(dataset), 50_000):
        block = dataset[start_at:min(start_at + 50_000, len(dataset))]
        for offset, row in enumerate(block):
            event_index = start_at + offset
            timestamp = int(row["time"])
            if timestamp > target_ns:
                return {"order": passive, "price": price, "queue_ahead": sum(q for _, q in ahead),
                        "stopped_by_refresh": stopped_by_refresh, "events_processed": event_index - start}
            action = bytes(row["action_type"]).strip()
            if action == b"F":
                stopped_by_refresh = True
                return {"order": passive, "price": price, "queue_ahead": sum(q for _, q in ahead),
                        "stopped_by_refresh": stopped_by_refresh, "events_processed": event_index - start}
            key = _key(row)
            order_id = key
            old = live_orders.get(order_id)
            old_qty = old[2] if old is not None else passive.ahead.get(order_id)
            old_side = old[0] if old is not None else None
            old_price = old[1] if old is not None else None
            if action == b"D":
                if old is not None and old_side == state["side_code"] and old_price == price:
                    passive.process(QueueEvent(timestamp, "cancel", passive.side, price, old_qty, order_id))
            elif action == b"Y":
                if old is not None and old_side == state["side_code"] and old_price == price:
                    passive.process(QueueEvent(timestamp, "cancel", passive.side, price, old_qty, order_id))
            elif action == b"M" and old is not None:
                row_side = _side(row)
                new_side = row_side if row_side is not None else old_side
                new_price = int(row["price"])
                new_price = old_price if new_price == -1 else new_price
                new_qty = int(row["volume"])
                new_qty = old_qty if new_qty == -1 else new_qty
                at_limit_before = old_side == state["side_code"] and old_price == price
                same_limit_after = new_side == state["side_code"] and new_price == price
                if at_limit_before and same_limit_after and new_qty < old_qty:
                    delta = old_qty - new_qty
                    if interpretation == "decrease_as_execution":
                        # Use any same-price M decrement as a trade-flow proxy.
                        # This lets volume consume earlier queue positions first.
                        passive.process(QueueEvent(timestamp, "trade", passive.side, price, delta, order_id))
                    elif order_id in passive.ahead:
                        passive.process(QueueEvent(timestamp, "cancel", passive.side, price, delta, order_id))
                elif at_limit_before and (not same_limit_after or new_qty > old_qty):
                    if order_id in passive.ahead:
                        passive.process(QueueEvent(timestamp, "cancel", passive.side, price, old_qty, order_id))
            # Apply the source update to the local live-order map.
            if action == b"F":
                live_orders.clear()
            elif action == b"D":
                live_orders.pop(order_id, None)
            elif action in (b"A", b"M", b"Y"):
                side = _side(row)
                row_price, row_qty = int(row["price"]), int(row["volume"])
                if action == b"A" or old is None:
                    if _resting(row):
                        live_orders[order_id] = (int(_side(row)), row_price, row_qty, int(row["priority_date"]))
                elif old is not None:
                    new_side = side if side is not None else old[0]
                    new_price = old[1] if row_price == -1 else row_price
                    new_qty = old[2] if row_qty == -1 else row_qty
                    new_priority = old[3] if int(row["priority_date"]) == -1 else int(row["priority_date"])
                    if new_price > 0 and new_qty > 0:
                        live_orders[order_id] = (new_side, new_price, new_qty, new_priority)
                    else:
                        live_orders.pop(order_id, None)
            if passive.remaining == 0:
                return {"order": passive, "price": price, "queue_ahead": sum(q for _, q in ahead),
                        "stopped_by_refresh": stopped_by_refresh, "events_processed": event_index - start + 1}
    return {"order": passive, "price": price, "queue_ahead": sum(q for _, q in ahead),
            "stopped_by_refresh": stopped_by_refresh, "events_processed": len(dataset) - start}


def _round_trip(side: str, entry_levels: list[tuple[int, int]], exit_levels: list[tuple[int, int]],
                requested: int, decision_mid: float, entry_fee_bps: float,
                exit_fee_bps: float) -> dict[str, Any]:
    entry = market_sweep(entry_levels, requested)
    entry_filled = int(entry["filled_quantity"])
    if not entry_filled:
        return {"entry_filled_quantity": 0, "exit_filled_quantity": 0,
                "completed_quantity": 0, "net_round_trip_bps": None,
                "net_bps_on_requested_notional": 0.0}
    exit = market_sweep(exit_levels, entry_filled)
    completed = int(exit["filled_quantity"])
    if not completed:
        return {"entry_filled_quantity": entry_filled, "exit_filled_quantity": 0,
                "completed_quantity": 0, "net_round_trip_bps": None,
                "net_bps_on_requested_notional": 0.0}
    entry_vwap, exit_vwap = float(entry["vwap"]), float(exit["vwap"])
    direction = 1.0 if side == "buy" else -1.0
    gross = direction * (exit_vwap - entry_vwap) / decision_mid * 10_000.0
    net = gross - entry_fee_bps * entry_vwap / decision_mid - exit_fee_bps * exit_vwap / decision_mid
    return {"entry_filled_quantity": entry_filled,
            "exit_filled_quantity": int(exit["filled_quantity"]),
            "completed_quantity": completed,
            "net_round_trip_bps": net,
            "net_bps_on_requested_notional": net * completed / requested}


def _summarize(rows: list[dict[str, Any]], label: str, entry_fee_bps: float,
               exit_fee_bps: float, bootstrap_reps: int) -> dict[str, Any]:
    if not rows:
        return {"orders": 0, "filled_orders": 0}
    fills = np.asarray([row["filled_quantity"] for row in rows], dtype=float)
    rates = np.asarray([row["fill_rate"] for row in rows], dtype=float)
    midpoint_markouts = [row["net_markout_bps"] for row in rows if row.get("net_markout_bps") is not None]
    per_order = [row["round_trip"]["net_bps_on_requested_notional"] for row in rows]
    daily: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        daily[row["date"]].append(float(row["round_trip"]["net_bps_on_requested_notional"]))
    ci: list[float | None] = [None, None]
    if daily and np.any(fills > 0):
        values = [float(np.mean(v)) for v in daily.values()]
        rng = np.random.default_rng(20261122)
        samples = np.empty(bootstrap_reps, dtype=float)
        for i in range(bootstrap_reps):
            sample = rng.integers(0, len(values), size=len(values))
            samples[i] = np.mean(np.asarray(values)[sample])
        ci = [float(np.quantile(samples, .025)), float(np.quantile(samples, .975))]
    completed = [row["round_trip"]["net_round_trip_bps"] for row in rows
                 if row["round_trip"]["completed_quantity"] > 0]
    attempted = [row for row in rows if row["filled_quantity"] > 0]
    return {
        "policy": label,
        "orders": len(rows),
        "filled_orders": int(np.sum(fills > 0)),
        "fill_probability_pct": float(np.mean(fills > 0) * 100),
        "mean_fill_fraction_pct": float(np.mean(rates) * 100),
        "mean_queue_ahead_shares_at_submission": float(np.mean([r.get("queue_ahead", 0) for r in rows])),
        "complete_round_trip_orders": sum(row["round_trip"]["completed_quantity"] == row["filled_quantity"] and row["filled_quantity"] > 0 for row in rows),
        "complete_round_trip_probability_pct": float(np.mean([row["round_trip"]["completed_quantity"] == row["filled_quantity"] and row["filled_quantity"] > 0 for row in rows]) * 100),
        "complete_exit_probability_given_entry_fill_pct": (float(np.mean([row["round_trip"]["completed_quantity"] == row["filled_quantity"] for row in attempted]) * 100) if attempted else None),
        "mean_round_trip_net_bps_on_completed_notional": float(np.mean(completed)) if completed else None,
        "mean_round_trip_net_bps_on_requested_notional": float(np.mean(per_order)),
        "requested_notional_date_bootstrap_95ci_bps": ci,
        "conditional_midpoint_markout_after_entry_fee_bps": float(np.mean(midpoint_markouts)) if midpoint_markouts else None,
        "conditional_midpoint_markout_date_bootstrap_95ci_bps": [None, None],
        "entry_fee_bps_assumption": entry_fee_bps,
        "exit_fee_bps_assumption": exit_fee_bps,
    }


def run(data_path: Path, signals_path: Path, output_json: Path, output_report: Path,
        event_stride: int, row_stride: int, order_size: int, fee_bps: float,
        bootstrap_reps: int, latency_ms: float, taker_fee_bps: float = 1.0) -> dict[str, Any]:
    if min(event_stride, row_stride, order_size, bootstrap_reps) < 1:
        raise ValueError("event stride, row stride, order size, and bootstrap replicates must be positive")
    if latency_ms < 0 or fee_bps < 0 or taker_fee_bps < 0:
        raise ValueError("latency and fee assumptions cannot be negative")
    signals = pd.read_csv(signals_path, dtype={"date": str})
    signals["date"] = signals["date"].str.zfill(8)
    signals = signals.loc[signals["prediction_hist_gradient_boosting"].isin(("up", "down"))].copy()
    signals = signals.sort_values(["date", "timestamp"], kind="stable")
    signals = pd.concat([group.iloc[::row_stride] for _, group in signals.groupby("date", sort=True)], ignore_index=True)
    signals = signals.rename(columns={"prediction_hist_gradient_boosting": "prediction"})
    with h5py.File(data_path, "r") as data:
        entry_states, target_books = _target_signal_states(data, signals, event_stride)
        states_by_key = entry_states
        outcomes: dict[str, list[dict[str, Any]]] = {"passive_execution_proxy": [],
            "passive_cancellation": [], "marketable_taker": []}
        candidate_count = 0
        for row in signals.to_dict(orient="records"):
            key = (row["date"], int(row["event_index"]))
            state = states_by_key.get(key)
            exit_book = target_books.get((row["date"], int(row["future_event_index"])))
            if state is None or exit_book is None:
                continue
            candidate_count += 1
            group = data[f"d{row['date']}"]["table"]
            for mode, label in (("decrease_as_execution", "passive_execution_proxy"),
                                ("decrease_as_cancel", "passive_cancellation")):
                simulation = _simulate_window(group, state, order_size, mode, int(latency_ms * 1e6))
                passive: PassiveOrder | None = simulation["order"]
                if passive is None:
                    filled, gross, net, fill_rate, limit_price = 0, None, None, 0.0, None
                else:
                    passive.cancel_remainder()
                    markout = passive.midpoint_markout(state["terminal_mid"], fee_bps_per_side=fee_bps)
                    filled = int(markout["filled_quantity"])
                    gross = markout["gross_markout_bps"]
                    net = markout["net_markout_bps"]
                    fill_rate = filled / order_size
                    limit_price = simulation["price"]
                exit_levels = exit_book["bids"] if state["side"] == "buy" else exit_book["asks"]
                passive_rt = (_round_trip(state["side"], [(limit_price, filled)], exit_levels,
                    order_size, float(row["mid"]), fee_bps, taker_fee_bps) if filled and limit_price else
                    _round_trip(state["side"], [], exit_levels, order_size, float(row["mid"]), fee_bps, taker_fee_bps))
                outcomes[label].append({
                    "date": state["date"],
                    "event_index": state["event_index"],
                    "prediction": row["prediction"],
                    "side": state["side"],
                    "limit_price": limit_price,
                    "queue_ahead": simulation.get("queue_ahead", sum(q for _, q in state["ahead"])),
                    "filled_quantity": filled,
                    "fill_rate": fill_rate,
                    "gross_markout_bps": gross,
                    "net_markout_bps": net,
                    "net_markout_on_requested_notional_bps": (float(net) * filled / order_size) if net is not None else 0.0,
                    "round_trip": passive_rt,
                    "events_processed": simulation["events_processed"],
                    "stopped_by_refresh": simulation["stopped_by_refresh"],
                })
            entry_levels = state["entry_asks"] if state["side"] == "buy" else state["entry_bids"]
            exit_levels = exit_book["bids"] if state["side"] == "buy" else exit_book["asks"]
            rt = _round_trip(state["side"], entry_levels, exit_levels, order_size,
                             float(row["mid"]), taker_fee_bps, taker_fee_bps)
            outcomes["marketable_taker"].append({"date": state["date"], "side": state["side"],
                "filled_quantity": rt["entry_filled_quantity"],
                "fill_rate": rt["entry_filled_quantity"] / order_size, "queue_ahead": 0,
                "round_trip": rt, "net_markout_bps": None})

    results = {name: _summarize(rows, name, fee_bps if name.startswith("passive") else taker_fee_bps,
                                taker_fee_bps, bootstrap_reps)
               for name, rows in outcomes.items()}
    metrics: dict[str, Any] = {
        "study": "WSELOB PEKAO queue-aware passive execution sensitivity",
        "data_source": data_path.name,
        "data_sha256": _sha256(data_path),
        "signals_source": signals_path.name,
        "signals_sha256": _sha256(signals_path),
        "selected_predictions": len(signals),
        "simulated_orders": candidate_count,
        "selection": f"Every {row_stride}th directional out-of-sample boosted-tree prediction per session.",
        "order_size_shares": order_size,
        "event_stride": event_stride,
        "latency_assumption": "Orders are placed after the first complete timestamp group at or after configured latency; this quantizes arrival to source event timestamps.",
        "configured_latency_ms": latency_ms,
        "entry_maker_fee_bps_assumption": fee_bps,
        "taker_fee_bps_per_side_assumption": taker_fee_bps,
        "bootstrap_replicates": bootstrap_reps,
        "queue_model": {
            "priority": "FIFO at the displayed best bid for up calls and best ask for down calls; order joins behind all visible same-price orders.",
            "additions": "New same-price orders after placement are behind the simulated order.",
            "deletions": "D events cancel the named order ahead without filling the simulated order.",
            "ambiguity_sensitivity": "WSELOB encodes order modifications and partial fills as M updates without an independent trade-print event; report contrasts treating same-price quantity decreases as executions versus cancellations.",
            "order_remainder": "Unfilled quantity is cancelled at the target horizon.",
        },
        "results": results,
        "interpretation": "Queue-aware passive fill sensitivity and displayed-book entry/exit comparison. Market impact, hidden liquidity, own-order interference, portfolio risk, and identified trade prints are not modeled; outcomes are not live strategy P&L.",
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Queue-Aware Passive Execution Sensitivity", "",
        "## Protocol", "",
        f"The study samples {candidate_count:,} directional boosted-tree calls from held-out PEKAO dates, using every {row_stride}th call per session. Up calls place a passive buy at the best bid; down calls place a passive sell at the best ask. Each shadow order queues behind visible same-price orders through the target quote. Arrival delay is {latency_ms:g} ms and is rounded to the first complete source timestamp group.", "",
        f"The passive entry pays an assumed {fee_bps:g} bp maker fee and exits by sweeping displayed opposite-side depth at the target quote, paying {taker_fee_bps:g} bp. The comparator immediately sweeps displayed depth at entry and again at the target, paying {taker_fee_bps:g} bp per side. P&L is reported per completed notional and per requested initial notional; unfilled entry quantity contributes zero to the latter.", "",
        "## Policy comparison", "",
        "| Policy / M-message assumption | Orders | Entry fill probability | Mean entry fill fraction | Completed round trips | Net bps / completed notional | Net bps / requested notional | Date-bootstrap 95% interval |", "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in results.items():
        ci = result["requested_notional_date_bootstrap_95ci_bps"]
        ci_text = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci[0] is not None else "n/a"
        label = {"passive_execution_proxy": "Passive (M decreases as execution proxy)",
                 "passive_cancellation": "Passive (M decreases as cancellations)",
                 "marketable_taker": "Marketable entry + exit"}.get(name, name)
        complete = result.get("mean_round_trip_net_bps_on_completed_notional")
        complete_text = f"{complete:.2f}" if complete is not None else "n/a"
        lines.append(f"| {label} | {result['orders']:,} | {result['fill_probability_pct']:.1f}% | {result['mean_fill_fraction_pct']:.1f}% | {result.get('complete_round_trip_probability_pct', 0):.1f}% | {complete_text} | {result['mean_round_trip_net_bps_on_requested_notional']:.2f} | {ci_text} |")
    lines.extend([
        "", "## Limits", "",
        "This is a displayed-book policy comparison, not a portfolio backtest. WSELOB M messages can mean execution or cancellation and do not identify trade prints; the two passive scenarios bracket that ambiguity. The marketable comparator and passive exit assume static displayed depth, with no impact, replenishment, hidden liquidity, own-order interference, or risk limits. Orders overlap and are not independent strategy trades. Results cover one stock in 2017 and the selected sampling schedule.", "",
        "Reproduce after running the WSELOB analysis and execution-aware benchmark:", "",
        "```powershell", "python execution_aware_benchmark.py --fee-bps-per-side 1", "python queue_execution_study.py", "python queue_execution_study.py --order-size 100 --output-json results/queue_execution_100share_metrics.json --output-report results/queue_execution_100share_study.md", "python queue_execution_study.py --latency-ms 100 --output-json results/queue_execution_latency100ms_metrics.json --output-report results/queue_execution_latency100ms_study.md", "```", "",
    ])
    output_report.write_text("\n".join(lines), encoding="utf-8")
    metrics["outputs"] = [str(output_json), str(output_report)]
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--signals", type=Path, default=DEFAULT_SIGNALS)
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "queue_execution_metrics.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "queue_execution_study.md")
    parser.add_argument("--event-stride", type=int, default=100)
    parser.add_argument("--row-stride", type=int, default=20, help="Use every nth out-of-sample directional prediction per session")
    parser.add_argument("--order-size", type=int, default=500)
    parser.add_argument("--maker-fee-bps", type=float, default=0.1)
    parser.add_argument("--bootstrap-reps", type=int, default=2000)
    parser.add_argument("--latency-ms", type=float, default=0.0)
    args = parser.parse_args()
    result = run(args.data, args.signals, args.output_json, args.output_report,
                 args.event_stride, args.row_stride, args.order_size,
                 args.maker_fee_bps, args.bootstrap_reps, args.latency_ms)
    print(json.dumps({"selected_predictions": result["selected_predictions"],
                      "results": result["results"], "outputs": [str(args.output_json), str(args.output_report)]}, indent=2))


if __name__ == "__main__":
    main()
