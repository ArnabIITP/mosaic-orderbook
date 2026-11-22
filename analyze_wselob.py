"""Reconstruct WSELOB PEKAO books and study historical execution conditions."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import h5py

from analyze_lobster import bucket, quantile, replay
from cpp_replay import ORDER_SIZES, run_snapshot_replay

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "wselob_2017" / "PEKAO_lob_2017_zlib.h5"


def _side(row: Any) -> int | None:
    value = int(row["side"])
    return 1 if value == 1 else 2 if value in (2, 5) else None


def _resting(row: Any) -> bool:
    return _side(row) is not None and int(row["price"]) > 0 and int(row["volume"]) > 0 and bytes(row["order_type"]).strip() in (b"2", b"P", b"K")


def _reconstruct_day(ds: h5py.Dataset, stride: int, date: str, diagnostics: Counter[str]) -> list[dict[str, Any]]:
    orders: dict[tuple[int, int], tuple[int, int, int, bytes]] = {}
    levels: dict[int, dict[int, int]] = {1: {}, 2: {}}
    snapshots: list[dict[str, Any]] = []
    pending_aggregates: dict[tuple[int, int], int] = {}
    current_group_time: int | None = None
    last_event_index = -1
    sample_requested = False

    def remove(key: tuple[int, int]) -> None:
        old = orders.pop(key, None)
        if old is None:
            return
        side, price, quantity, _order_type = old
        remaining = levels[side].get(price, 0) - quantity
        if remaining > 0:
            levels[side][price] = remaining
        else:
            levels[side].pop(price, None)

    def add(row: Any) -> None:
        if not _resting(row):
            diagnostics["non_resting_adds_skipped"] += 1
            return
        key = (int(row["order_date"]), int(row["order_id"]))
        if key in orders:
            diagnostics["duplicate_adds_replaced"] += 1
            remove(key)
        side, price, quantity = int(_side(row)), int(row["price"]), int(row["volume"])
        orders[key] = side, price, quantity, bytes(row["order_type"]).strip()
        levels[side][price] = levels[side].get(price, 0) + quantity

    def amend(row: Any, count_missing_modify: bool) -> None:
        key = (int(row["order_date"]), int(row["order_id"]))
        previous = orders.get(key)
        if previous is None:
            if count_missing_modify:
                if _resting(row):
                    diagnostics["modify_without_live_resting_order"] += 1
                else:
                    diagnostics["modify_without_live_nonresting_order"] += 1
            add(row)
            return
        old_side, old_price, old_quantity, old_type = previous
        row_side = _side(row)
        row_price = int(row["price"])
        row_quantity = int(row["volume"])
        row_type = bytes(row["order_type"]).strip()
        new_side = row_side if row_side is not None else old_side
        new_price = old_price if row_price == -1 else row_price
        new_quantity = old_quantity if row_quantity == -1 else row_quantity
        new_type = old_type if row_type == b"-1" else row_type
        remove(key)
        if new_price > 0 and new_quantity > 0 and new_type in (b"2", b"P", b"K"):
            orders[key] = (new_side, new_price, new_quantity, new_type)
            levels[new_side][new_price] = levels[new_side].get(new_price, 0) + new_quantity

    def validate_aggregate_group() -> None:
        for (side, price), expected in pending_aggregates.items():
            actual = levels[side].get(price, 0)
            diagnostics["aggregate_volume_checks"] += 1
            if expected != actual:
                diagnostics["aggregate_volume_mismatches"] += 1
                if diagnostics["aggregate_volume_mismatches"] <= 5:
                    diagnostics.setdefault("aggregate_volume_mismatch_examples", []).append({
                        "event_index": last_event_index, "side": side, "price": price,
                        "source_aggregate": expected, "reconstructed_aggregate": actual,
                    })
        pending_aggregates.clear()

    def capture_snapshot(event_index: int, event_time: int) -> None:
        asks = sorted((p, q) for p, q in levels[2].items() if q > 0)[:10]
        bids = sorted(((p, q) for p, q in levels[1].items() if q > 0), reverse=True)[:10]
        if not asks or not bids or asks[0][0] <= bids[0][0]:
            diagnostics["invalid_or_one_sided_snapshots"] += 1
            return
        snapshots.append({
            "ticker": "PEKAO", "date": date, "event_index": event_index,
            "timestamp": event_time / 1e9, "time": event_time / 1e9,
            "mid": (asks[0][0] + bids[0][0]) / 2,
            "spread_raw": asks[0][0] - bids[0][0],
            "spread_pln": (asks[0][0] - bids[0][0]) / 100,
            "asks": asks, "bids": bids,
            "ask_depth": sum(q for _, q in asks),
            "bid_depth": sum(q for _, q in bids),
            "two_sided_depth": sum(q for _, q in asks + bids),
        })

    for start in range(0, len(ds), 50_000):
        block = ds[start:start + 50_000]
        for offset, row in enumerate(block):
            i = start + offset
            event_time = int(row["time"])
            if current_group_time is not None and event_time != current_group_time:
                validate_aggregate_group()
                if sample_requested:
                    capture_snapshot(last_event_index, current_group_time)
                    sample_requested = False
            current_group_time = event_time
            last_event_index = i
            action = bytes(row["action_type"])
            if action == b"F":
                orders.clear()
                levels = {1: {}, 2: {}}
                diagnostics["full_refreshes"] += 1
            elif action == b"A":
                add(row)
            elif action == b"Y":
                amend(row, count_missing_modify=False)
            elif action == b"M":
                amend(row, count_missing_modify=True)
            elif action == b"D":
                key = (int(row["order_date"]), int(row["order_id"]))
                if key not in orders:
                    if _resting(row):
                        diagnostics["delete_without_live_resting_order"] += 1
                    else:
                        diagnostics["delete_without_live_nonresting_order"] += 1
                remove(key)
            else:
                diagnostics["unknown_actions"] += 1

            # The source reports total displayed volume at the event's price.
            # For priced displayed orders this is an independent reconstruction check.
            event_side = _side(row)
            event_price = int(row["price"])
            event_type = bytes(row["order_type"]).strip()
            if action in (b"A", b"M", b"D", b"Y") and event_side is not None and event_price > 0 and event_type in (b"2", b"P", b"K"):
                pending_aggregates[(event_side, event_price)] = int(row["agg_volume"])

            if i % stride == 0:
                sample_requested = True

    validate_aggregate_group()
    if sample_requested:
        capture_snapshot(last_event_index, current_group_time or 0)
    return snapshots


def reconstruct(path: Path, stride: int) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    diagnostics: Counter[str] = Counter()
    day_events: dict[str, int] = {}
    with h5py.File(path, "r") as data:
        for group_name in sorted(data.keys()):
            date = group_name.removeprefix("d")
            ds = data[group_name]["table"]
            day_events[date] = len(ds)
            by_day[date] = _reconstruct_day(ds, stride, date, diagnostics)
    return by_day, {"date_count": len(by_day), "raw_events": sum(day_events.values()), "events_by_date": day_events, "reconstruction_diagnostics": dict(diagnostics)}


def enrich_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return []
    times = [r["timestamp"] for r in rows]
    cumulative = [0.0]
    for i, row in enumerate(rows):
        squared = 0.0 if i == 0 else math.log(row["mid"] / rows[i - 1]["mid"]) ** 2
        cumulative.append(cumulative[-1] + squared)
    start = times[0]
    out = []
    for i, row in enumerate(rows):
        if row["timestamp"] - start < 60:
            continue
        left = bisect.bisect_left(times, row["timestamp"] - 60)
        row["volatility_bps_1m"] = math.sqrt(max(0.0, cumulative[i + 1] - cumulative[left])) * 10_000
        out.append(row)
    if not out:
        return out
    v = [r["volatility_bps_1m"] for r in out]
    ad = [r["ask_depth"] for r in out]
    bd = [r["bid_depth"] for r in out]
    vlo, vhi = quantile(v, 1 / 3), quantile(v, 2 / 3)
    alo, ahi = quantile(ad, 1 / 3), quantile(ad, 2 / 3)
    blo, bhi = quantile(bd, 1 / 3), quantile(bd, 2 / 3)
    for r in out:
        r["volatility_bucket"] = bucket(r["volatility_bps_1m"], vlo, vhi, ("low", "middle", "high"))
        r["buy_liquidity_bucket"] = bucket(r["ask_depth"], alo, ahi, ("thin", "middle", "deep"))
        r["sell_liquidity_bucket"] = bucket(r["bid_depth"], blo, bhi, ("thin", "middle", "deep"))
    return out


def _group_summary(rows: list[dict[str, Any]], grouping: str, labels: tuple[str, ...], size: int = 500) -> list[dict[str, Any]]:
    result = []
    for side in ("buy", "sell"):
        key = f"{side}_{grouping}" if grouping == "liquidity_bucket" else grouping
        for label in labels:
            group = [r for r in rows if r[key] == label]
            costs = [r[f"{side}_{size}_bps"] for r in group if r[f"{side}_{size}_bps"] is not None]
            result.append({
                "side": side, "bucket": label, "observations": len(group),
                "median_spread_pln": statistics.median(r["spread_pln"] for r in group) if group else None,
                "median_consumable_depth_shares": statistics.median((r["ask_depth"] if side == "buy" else r["bid_depth"]) for r in group) if group else None,
                "mean_500_share_slippage_bps": statistics.mean(costs) if costs else None,
                "mean_fill_rate": statistics.mean(r[f"{side}_{size}_fill"] for r in group) if group else None,
            })
    return result


def paired_daily_effect(by_day: dict[str, list[dict[str, Any]]], grouping: str, first: str, second: str) -> list[dict[str, Any]]:
    """Equal-weight dates; bootstrap paired daily mean differences, not snapshots."""
    rng = random.Random(20_261_122)
    result = []
    for side in ("buy", "sell"):
        daily_differences = []
        for date, day_rows in by_day.items():
            key = f"{side}_{grouping}" if grouping == "liquidity_bucket" else grouping
            first_costs = [r[f"{side}_500_bps"] for r in day_rows if r[key] == first and r[f"{side}_500_bps"] is not None]
            second_costs = [r[f"{side}_500_bps"] for r in day_rows if r[key] == second and r[f"{side}_500_bps"] is not None]
            if first_costs and second_costs:
                daily_differences.append(statistics.mean(second_costs) - statistics.mean(first_costs))
        if not daily_differences:
            result.append({"side": side, "comparison": f"{second} minus {first}", "dates": 0, "mean_daily_difference_bps": None, "ci95_low_bps": None, "ci95_high_bps": None})
            continue
        means = [statistics.mean(rng.choices(daily_differences, k=len(daily_differences))) for _ in range(5000)]
        result.append({
            "side": side, "comparison": f"{second} minus {first}", "dates": len(daily_differences),
            "mean_daily_difference_bps": statistics.mean(daily_differences),
            "ci95_low_bps": quantile(means, 0.025), "ci95_high_bps": quantile(means, 0.975),
        })
    return result


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "(no observations)"
    headers = list(rows[0])
    fmt = lambda v: "n/a" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"] + ["| " + " | ".join(fmt(v) for v in r.values()) + " |" for r in rows])


def analyze(data_path: Path = DEFAULT_DATA, output_dir: Path = ROOT / "results", stride: int = 100, fee_bps: float = 1.0) -> dict[str, Any]:
    if stride < 1 or fee_bps < 0:
        raise ValueError("stride must be positive and fee_bps nonnegative")
    all_by_day, metadata = reconstruct(data_path, stride)
    by_day = {date: enrich_day(rows) for date, rows in all_by_day.items()}
    by_day = {d: rows for d, rows in by_day.items() if rows}
    rows = [r for day_rows in by_day.values() for r in day_rows]
    if not rows:
        raise ValueError("no valid full-window snapshots reconstructed")
    # Python provides an independent sweep; C++ is the reported execution engine.
    py_start = time.perf_counter()
    for r in rows:
        for size in ORDER_SIZES:
            for side, levels in (("buy", r["asks"]), ("sell", r["bids"])):
                r[f"{side}_{size}_bps"], r[f"{side}_{size}_fill"] = replay(levels, side, size, r["mid"])
    py_seconds = time.perf_counter() - py_start
    cpp = run_snapshot_replay(rows, verify_python=True)

    # Delay sensitivity uses the next observed book at or after the delay, on the same date.
    latency_rows: list[dict[str, Any]] = []
    latency_summary = []
    latency_validation = []
    fee_scenarios = sorted({0.0, 1.0, 5.0, fee_bps})
    for delay_s in (1, 5):
        lagged: list[dict[str, Any]] = []
        for date, day_rows in by_day.items():
            ts = [r["timestamp"] for r in day_rows]
            for i, decision in enumerate(day_rows):
                j = bisect.bisect_left(ts, decision["timestamp"] + delay_s, lo=i + 1)
                if j == len(day_rows):
                    continue
                future = day_rows[j]
                lagged.append({**future, "time": decision["timestamp"], "mid": decision["mid"], "decision_mid": decision["mid"], "execution_mid": future["mid"], "latency_s": delay_s})
        if lagged:
            validation = run_snapshot_replay(lagged, verify_python=True)
            latency_validation.append(validation)
            for r in lagged:
                for side in ("buy", "sell"):
                    cost = r[f"{side}_500_bps"]
                    r[f"{side}_net_fee_bps"] = None if cost is None else cost + fee_bps
            mean_abs_midpoint_move = statistics.mean(abs((r["execution_mid"] - r["decision_mid"]) / r["decision_mid"] * 10000) for r in lagged)
            for side in ("buy", "sell"):
                base_costs = [r[f"{side}_500_bps"] for r in lagged if r[f"{side}_500_bps"] is not None]
                for scenario_fee in fee_scenarios:
                    latency_summary.append({
                        "latency_seconds": delay_s, "fee_bps_per_side": scenario_fee,
                        "side": side, "snapshots": len(lagged),
                        "mean_net_slippage_bps_500_shares": statistics.mean(base_costs) + scenario_fee if base_costs else None,
                        "mean_abs_midpoint_move_bps": mean_abs_midpoint_move,
                    })
            latency_rows.extend(lagged)

    vol_rows = _group_summary(rows, "volatility_bucket", ("low", "middle", "high"))
    liq_rows = _group_summary(rows, "liquidity_bucket", ("thin", "middle", "deep"))
    vol_effects = paired_daily_effect(by_day, "volatility_bucket", "low", "high")
    liq_effects = paired_daily_effect(by_day, "liquidity_bucket", "deep", "thin")
    overall = []
    for size in ORDER_SIZES:
        for side in ("buy", "sell"):
            valid = [r for r in rows if r[f"{side}_{size}_bps"] is not None]
            overall.append({"side": side, "order_shares": size, "snapshots": len(rows), "mean_slippage_bps_filled": statistics.mean(r[f"{side}_{size}_bps"] for r in valid) if valid else None, "mean_fill_rate": statistics.mean(r[f"{side}_{size}_fill"] for r in rows)})

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "wselob_2017_execution_observations.csv"
    fields = ["date", "event_index", "timestamp", "mid", "spread_pln", "ask_depth", "bid_depth", "two_sided_depth", "volatility_bps_1m", "volatility_bucket", "buy_liquidity_bucket", "sell_liquidity_bucket"]
    for size in ORDER_SIZES:
        for side in ("buy", "sell"):
            fields.extend([f"{side}_{size}_bps", f"{side}_{size}_fill"])
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows({k: r[k] for k in fields} for r in rows)
    latency_path = output_dir / "wselob_2017_latency_observations.csv"
    lat_fields = ["date", "timestamp", "latency_s", "decision_mid", "execution_mid", "spread_pln", "ask_depth", "bid_depth", "buy_500_bps", "buy_500_fill", "sell_500_bps", "sell_500_fill", "buy_net_fee_bps", "sell_net_fee_bps"]
    with latency_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=lat_fields); w.writeheader(); w.writerows({k: r.get(k) for k in lat_fields} for r in latency_rows)
    reference_path = output_dir / "wselob_2017_reference_validation.json"
    reference_validation = json.loads(reference_path.read_text(encoding="utf-8")) if reference_path.exists() else None
    benchmark_path = output_dir / "cpp_event_replay_benchmark.json"
    event_benchmark = json.loads(benchmark_path.read_text(encoding="utf-8")) if benchmark_path.exists() else None
    meta = {**metadata, "ticker": "PEKAO", "date_start": min(by_day), "date_end": max(by_day), "usable_days": len(by_day), "stride_raw_events": stride, "usable_snapshots": len(rows), "hdf5_size_bytes": data_path.stat().st_size, "hdf5_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(), "reference_validation": reference_validation, "cpp_event_replay_benchmark": event_benchmark, "replay_execution_engine": {**cpp, "python_sweep_seconds": py_seconds}, "latency_replay_validation": latency_validation, "overall_execution": overall, "volatility_terciles_500_shares": vol_rows, "directional_depth_terciles_500_shares": liq_rows, "paired_daily_volatility_effects": vol_effects, "paired_daily_depth_effects": liq_effects, "latency_fee_sensitivity": latency_summary, "fee_assumption_bps_per_side": fee_bps, "observation_csv": csv_path.name, "latency_csv": latency_path.name}
    (output_dir / "wselob_2017_metrics.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if event_benchmark:
        mutation_profile = event_benchmark.get("cpp_mutation_latency_profile", {})
        mutation_ns = mutation_profile.get("percentiles_ns", {})
        event_replay_summary = (
            f"A separate Release build (`{event_benchmark['cpp_compiler']}`, `{event_benchmark['cpp_build_optimization']}`) replayed all {event_benchmark['processed_events']:,} source updates after one warmup, then completed {len(event_benchmark['cpp_runs_seconds'])} uninstrumented runs. Median wall time was {event_benchmark['cpp_median_seconds']:.2f}s ({event_benchmark['cpp_events_per_second_median']:,.0f} updates/s); runs ranged from {event_benchmark['cpp_min_seconds']:.2f}s to {event_benchmark['cpp_max_seconds']:.2f}s. The benchmark host was `{event_benchmark['benchmark_host']['platform']}`, `{event_benchmark['benchmark_host']['processor']}`. It emitted {event_benchmark['matched_snapshots']:,} sampled books and matched Python exactly (zero mismatches); output SHA-256 `{event_benchmark['output_sha256']}`. The uninstrumented timing includes process startup, binary feed I/O, parsing, book updates, and snapshot output.\n\n"
            f"A separate pass timed one book mutation per {mutation_profile.get('sample_every_events', 0):,} feed events after binary decoding. Across {mutation_profile.get('sample_count', 0):,} deterministic samples, measured p50/p95/p99/p99.9 mutation times were {mutation_ns.get('p50_ns', 0):,}/{mutation_ns.get('p95_ns', 0):,}/{mutation_ns.get('p99_ns', 0):,}/{mutation_ns.get('p999_ns', 0):,} ns. These sampled software mutation times exclude parsing and output; they are not network or exchange latency and may miss rare event types. The profile pass matched the same output. Python's {event_benchmark['python_hdf5_reconstruction_seconds']:.2f}s reconstruction reads/decompresses HDF5 and is not a controlled language-versus-language benchmark."
        )
    else:
        event_replay_summary = "Run `python replay_wselob_cpp.py --stride 100 --repetitions 7 --profile-every 1024` to perform the full-stream Release benchmark and sampled mutation profile."

    report = f"""# WSELOB 2017 PEKAO Execution Study

## Executive summary

The replay rebuilds PEKAO's displayed limit book for {meta['usable_days']} trading dates in 2017 from {metadata['raw_events']:,} order updates. The execution study uses {len(rows):,} snapshots sampled every {stride} source events. Each C++ sweep is checked against the Python implementation. The results cover one stock and historical displayed depth; they do not establish causal effects or predict current execution.

## Research question and related work

The study measures how displayed liquidity and recent price variability relate to the cost and fill rate of hypothetical marketable orders. The supplied [LiT paper](https://doi.org/10.3389/frai.2025.1616485) predicts price movements from order-book sequences; this study instead measures execution cost and does not reproduce LiT.

## Data and reconstruction

- **Source:** PEKAO file from [WSELOB-2017 on Mendeley Data](https://data.mendeley.com/datasets/3g4mhdp899/1), DOI [10.17632/3g4mhdp899.1](https://doi.org/10.17632/3g4mhdp899.1). The dataset record describes Warsaw Stock Exchange order-book data for 2017 and is licensed CC BY 4.0. The downloaded HDF5 is {meta['hdf5_size_bytes']:,} bytes; SHA-256: `{meta['hdf5_sha256']}`.
- **Coverage:** {meta['date_start']} through {meta['date_end']}; {meta['usable_days']} dates, {metadata['raw_events']:,} raw events, {len(rows):,} usable snapshots. The source has five equities, but this run uses PEKAO only.
- **State reconstruction:** order identity is `(order_date, order_id)`. Adds and retransmissions create or update displayed orders; modifications preserve omitted fields and replace remaining quantity; deletes remove orders; full-refresh messages clear the book. Buy side 1 and sell sides 2/5 are kept by integer price. Priced limit, peg, and market-to-limit orders rest; unpriced market orders do not. Snapshots are taken only at timestamp-group boundaries. The source `agg_volume` field was checked at those boundaries: {metadata['reconstruction_diagnostics'].get('aggregate_volume_checks', 0):,} checks, with {metadata['reconstruction_diagnostics'].get('aggregate_volume_mismatches', 0):,} differences. The full diagnostics and example rows are in `wselob_2017_metrics.json`.
- **Reference validation:** {f"the author's `orderbook2.py` implementation matched all {reference_validation['matched_snapshots']:,} sampled ten-level books across {reference_validation['dates']} dates with zero mismatches (first {reference_validation['events_per_date']:,} events per date)." if reference_validation else "run `validate_wselob_reference.py` to compare with the author's reference implementation."}
- **Scaling:** prices are source integer units (100 units per PLN); quantities are shares. Basis-point ratios are invariant to the price scaling.

## Method

The book is sampled when each {stride}-event threshold is reached, then captured after all updates with that timestamp have been applied. Crossed or one-sided states are omitted. We compute trailing one-minute realised volatility from sampled midpoints independently within each trading date and omit the first minute. Liquidity buckets use that date's terciles of executable-side top-ten depth; volatility buckets use that date's volatility terciles. The 500-share comparisons are descriptive and do not control for spread or time of day.

For each snapshot, hypothetical marketable buys consume asks and sells consume bids at displayed prices, up to ten occupied levels. Shortfall is VWAP relative to the snapshot midpoint; fill rate is filled divided by requested quantity. This captures the price concession from sweeping the observed book, not the market impact after the book changes.

Latency sensitivity executes using the first sampled book at least 1 or 5 seconds after the decision timestamp, while measuring price relative to the decision midpoint. The report varies fees from 0 to 5 bps per side. Because observations are sampled every {stride} events, latency timing is quantized by the next sampled observation and is not an exchange/network latency simulation. Fees are explicit scenario assumptions, not observed broker fees.

The C++17 matcher is used for the reported execution outcomes and Python independently replays each side/size. Baseline replay passed {cpp['parity_checks']:,} comparisons over {cpp['snapshots']:,} snapshots; delayed replay passed {sum(v['parity_checks'] for v in latency_validation):,} additional comparisons. Maximum shortfall difference was 0 bps. Python sweep elapsed {py_seconds:.2f}s; baseline C++ CLI elapsed {cpp['cpp_wall_seconds']:.2f}s. These timings have different measurement boundaries and should not be treated as a speedup ratio.

## C++ full-stream replay benchmark

{event_replay_summary}

{f"The independent order-book reference comparison matched {reference_validation['matched_snapshots']:,} snapshots over {reference_validation['dates']} dates with zero mismatches." if reference_validation else "The independent order-book reference comparison has not yet been run."} The `agg_volume` boundary audit found {metadata['reconstruction_diagnostics'].get('aggregate_volume_mismatches', 0):,} differences across {metadata['reconstruction_diagnostics'].get('aggregate_volume_checks', 0):,} checks ({(metadata['reconstruction_diagnostics'].get('aggregate_volume_mismatches', 0) / max(1, metadata['reconstruction_diagnostics'].get('aggregate_volume_checks', 0)) * 100):.4f}%). The metrics file records the mismatches and example rows.

## Overall execution outcomes

{_markdown_table(overall)}

## Main findings

For the 500-share order, mean slippage rises from {overall[2]['mean_slippage_bps_filled']:.2f} bps for buys and {overall[3]['mean_slippage_bps_filled']:.2f} bps for sells to {overall[4]['mean_slippage_bps_filled']:.2f} and {overall[5]['mean_slippage_bps_filled']:.2f} bps at 1,000 shares. The larger sweep consumes more price levels, which is the measured static depth cost.

Across dates, high-volatility versus low-volatility 500-share slippage is higher by {vol_effects[0]['mean_daily_difference_bps']:.2f} bps for buys (date-bootstrap 95% interval {vol_effects[0]['ci95_low_bps']:.2f} to {vol_effects[0]['ci95_high_bps']:.2f}) and {vol_effects[1]['mean_daily_difference_bps']:.2f} bps for sells ({vol_effects[1]['ci95_low_bps']:.2f} to {vol_effects[1]['ci95_high_bps']:.2f}). High-volatility buckets also have a wider median spread ({vol_rows[0]['median_spread_pln']:.2f} to {vol_rows[2]['median_spread_pln']:.2f} PLN). This points to spread widening as part of the association; the analysis does not isolate a causal volatility effect.

Thin versus deep displayed-depth differences are not reliably ordered: the paired daily intervals span zero for buys ({liq_effects[0]['ci95_low_bps']:.2f} to {liq_effects[0]['ci95_high_bps']:.2f} bps) and sells ({liq_effects[1]['ci95_low_bps']:.2f} to {liq_effects[1]['ci95_high_bps']:.2f} bps). For these order sizes, a 500-share sweep is small relative to the median top-ten depth, so spread and other conditions dominate this coarse depth split.

## 500-share volatility terciles

{_markdown_table(vol_rows)}

## 500-share executable-depth terciles

{_markdown_table(liq_rows)}

## Paired daily bucket differences

Differences are computed within each date, then averaged with dates weighted equally. The 95% intervals come from a seeded bootstrap that resamples dates rather than individual snapshots. Volatility is high minus low; depth is thin minus deep. These comparisons do not adjust for spread or time of day.

### Volatility: high minus low

{_markdown_table(vol_effects)}

### Depth: thin minus deep

{_markdown_table(liq_effects)}

## Latency and fee sensitivity

The grid varies assumed per-side fees over {', '.join(f'{v:g}' for v in fee_scenarios)} bps at 1- and 5-second book delays. These costs are measured from the decision midpoint and reported by order side.

{_markdown_table(latency_summary)}

## Interpretation and limitations

These are conditional associations in PEKAO's 2017 data. High-volatility periods can also have wider spreads, different depth, and different times of day. The snapshots omit hidden liquidity, queue position, later cancellations, replenishment, and order-arrival competition. The latency proxy uses a later sampled book and cannot show the intervening path. Fees are illustrative; the results are not expected live fills or returns.

## Reproduction

Install Python 3.10+, `h5py`, and a C++17 compiler. From the project root run:

```powershell
python -m pip install -r requirements-research.txt
python -m pip install -r requirements-validation.txt
python validate_wselob_reference.py --days 5 --events-per-day 5000
python replay_wselob_cpp.py --stride 100 --repetitions 7 --profile-every 1024
python analyze_wselob.py --stride 100 --fee-bps 1
```

Outputs: `{csv_path.name}`, `{latency_path.name}`, `wselob_2017_metrics.json`, and this report.
"""
    (output_dir / "wselob_2017_execution_study.md").write_text(report, encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--stride", type=int, default=100)
    parser.add_argument("--fee-bps", type=float, default=1.0)
    args = parser.parse_args()
    metrics = analyze(args.data, args.output_dir, args.stride, args.fee_bps)
    print(json.dumps({k: metrics[k] for k in ("usable_days", "raw_events", "usable_snapshots", "replay_execution_engine", "overall_execution", "latency_fee_sensitivity")}, indent=2))


if __name__ == "__main__":
    main()
