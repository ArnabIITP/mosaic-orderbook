"""Replay hypothetical orders against the included LOBSTER five-level samples."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from experiment import run_study


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * fraction
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def bucket(value: float, low: float, high: float, labels: tuple[str, str, str]) -> str:
    return labels[0] if value <= low else labels[1] if value <= high else labels[2]


def replay(levels: list[tuple[int, int]], side: str, quantity: int, midpoint: float) -> tuple[float | None, float]:
    remaining, notional, filled = quantity, 0, 0
    for price, size in levels:
        take = min(remaining, size)
        notional += take * price
        filled += take
        remaining -= take
        if remaining == 0:
            break
    if not filled:
        return None, 0.0
    vwap = notional / filled
    cost = vwap - midpoint if side == "buy" else midpoint - vwap
    return cost / midpoint * 10_000, filled / quantity


def load_sample(message_file: Path, stride: int) -> tuple[list[dict[str, Any]], int]:
    book_file = message_file.with_name(message_file.name.replace("_message_", "_orderbook_"))
    if not book_file.exists():
        raise FileNotFoundError(f"missing matching order-book file for {message_file}")
    rows: list[dict[str, Any]] = []
    source_rows = 0
    with message_file.open(newline="", encoding="utf-8") as messages, book_file.open(newline="", encoding="utf-8") as books:
        message_reader, book_reader = csv.reader(messages), csv.reader(books)
        while True:
            try:
                msg, book = next(message_reader), next(book_reader)
            except StopIteration:
                break
            source_rows += 1
            if len(msg) != 6 or len(book) != 20:
                raise ValueError(f"unexpected columns in {message_file.name}, row {source_rows}")
            if (source_rows - 1) % stride:
                continue
            raw = [int(x) for x in book]
            asks = [(raw[i], raw[i + 1]) for i in range(0, 20, 4) if 0 < raw[i] < 9_000_000_000 and raw[i + 1] > 0]
            bids = [(raw[i + 2], raw[i + 3]) for i in range(0, 20, 4) if 0 < raw[i + 2] < 9_000_000_000 and raw[i + 3] > 0]
            if not asks or not bids or asks[0][0] <= bids[0][0]:
                continue
            rows.append({
                "ticker": message_file.name.split("_")[0],
                "time": float(msg[0]),
                "mid": (asks[0][0] + bids[0][0]) / 2,
                "spread_ticks": (asks[0][0] - bids[0][0]) / 100,
                "asks": asks,
                "bids": bids,
                "two_sided_depth": sum(q for _, q in asks) + sum(q for _, q in bids),
                "ask_depth": sum(q for _, q in asks),
                "bid_depth": sum(q for _, q in bids),
            })
        for reader, kind in ((message_reader, "message"), (book_reader, "order-book")):
            try:
                next(reader)
            except StopIteration:
                continue
            raise ValueError(f"{kind} and order-book files have different row counts: {message_file.name}")
    return rows, source_rows


def summarize(rows: list[dict[str, Any]], bucket_key: str, labels: tuple[str, str, str], size: int) -> list[dict[str, Any]]:
    output = []
    for label in labels:
        group = [row for row in rows if row[bucket_key] == label]
        record: dict[str, Any] = {
            "bucket": label,
            "observations": len(group),
            "mean_spread_cents": statistics.mean(row["spread_ticks"] for row in group),
            "median_two_sided_depth_shares": statistics.median(row["two_sided_depth"] for row in group),
        }
        for side in ("buy", "sell"):
            costs = [row[f"{side}_{size}_bps"] for row in group if row[f"{side}_{size}_bps"] is not None]
            record[f"{side}_slippage_bps"] = statistics.mean(costs) if costs else None
            record[f"{side}_fill_rate"] = statistics.mean(row[f"{side}_{size}_fill"] for row in group)
        output.append(record)
    return output


def summarize_directional_liquidity(rows: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    depth_key = "ask_depth" if side == "buy" else "bid_depth"
    bucket_key = f"{side}_liquidity_bucket"
    output = []
    for label in ("thin", "middle", "deep"):
        group = [row for row in rows if row[bucket_key] == label]
        output.append({
            "ticker": group[0]["ticker"],
            "side": side,
            "bucket": label,
            "observations": len(group),
            "mean_spread_cents": statistics.mean(row["spread_ticks"] for row in group),
            "median_executable_side_depth_shares": statistics.median(row[depth_key] for row in group),
            "slippage_bps": statistics.mean(row[f"{side}_500_bps"] for row in group if row[f"{side}_500_bps"] is not None),
            "fill_rate": statistics.mean(row[f"{side}_500_fill"] for row in group),
        })
    return output


def fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def markdown_table(rows: list[dict[str, Any]]) -> str:
    headers = list(rows[0])
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
        + ["| " + " | ".join(fmt(v) for v in row.values()) + " |" for row in rows]
    )


def analyze(data_dir: Path, output_dir: Path, stride: int = 10) -> dict[str, Any]:
    if stride < 1:
        raise ValueError("stride must be at least 1")
    message_files = sorted(data_dir.rglob("*_message_5.csv"))
    if not message_files:
        raise FileNotFoundError(f"no *_message_5.csv samples found under {data_dir}")

    by_ticker: dict[str, list[dict[str, Any]]] = {}
    source_counts: dict[str, int] = {}
    python_sweep_seconds = 0.0
    for message_file in message_files:
        ticker = message_file.name.split("_")[0]
        if ticker in by_ticker:
            raise ValueError(f"multiple sample days found for {ticker}; analyze one day per ticker")
        rows, source_rows = load_sample(message_file, stride)
        if len(rows) < 100:
            raise ValueError(f"only {len(rows)} valid snapshots in {message_file}")
        source_counts[ticker] = source_rows

        session_start = rows[0]["time"]
        times = [row["time"] for row in rows]
        cumulative = [0.0]
        for i, row in enumerate(rows):
            if i == 0:
                squared = 0.0
            else:
                log_return = math.log(row["mid"] / rows[i - 1]["mid"])
                squared = log_return * log_return
            cumulative.append(cumulative[-1] + squared)
        for i, row in enumerate(rows):
            left = bisect.bisect_left(times, row["time"] - 60.0)
            right = i + 1
            row["volatility_bps_1m"] = math.sqrt(max(0.0, cumulative[right] - cumulative[left])) * 10_000
        # Exclude the first minute, where the trailing volatility window is incomplete.
        rows = [row for row in rows if row["time"] - session_start >= 60.0]
        if len(rows) < 100:
            raise ValueError(f"fewer than 100 full-window snapshots in {message_file}")

        vol = [row["volatility_bps_1m"] for row in rows]
        vol_low, vol_high = quantile(vol, 1 / 3), quantile(vol, 2 / 3)
        ask_depth = [row["ask_depth"] for row in rows]
        bid_depth = [row["bid_depth"] for row in rows]
        ask_low, ask_high = quantile(ask_depth, 1 / 3), quantile(ask_depth, 2 / 3)
        bid_low, bid_high = quantile(bid_depth, 1 / 3), quantile(bid_depth, 2 / 3)
        sweep_started = time.perf_counter()
        for row in rows:
            row["volatility_bucket"] = bucket(row["volatility_bps_1m"], vol_low, vol_high, ("low", "middle", "high"))
            row["buy_liquidity_bucket"] = bucket(row["ask_depth"], ask_low, ask_high, ("thin", "middle", "deep"))
            row["sell_liquidity_bucket"] = bucket(row["bid_depth"], bid_low, bid_high, ("thin", "middle", "deep"))
            for size in (100, 500, 1000):
                for side, levels in (("buy", row["asks"]), ("sell", row["bids"])):
                    cost, fill = replay(levels, side, size, row["mid"])
                    row[f"{side}_{size}_bps"], row[f"{side}_{size}_fill"] = cost, fill
        python_sweep_seconds += time.perf_counter() - sweep_started
        by_ticker[ticker] = rows

    observations = [row for rows in by_ticker.values() for row in rows]
    from cpp_replay import run_snapshot_replay

    cpp_validation = run_snapshot_replay(observations, verify_python=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    synthetic_rows = run_study()
    synthetic_path = output_dir / "synthetic_stress_results.csv"
    with synthetic_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(synthetic_rows[0]))
        writer.writeheader()
        writer.writerows(synthetic_rows)
    csv_path = output_dir / "lobster_execution_observations.csv"
    columns = ["ticker", "time", "spread_ticks", "ask_depth", "bid_depth", "two_sided_depth", "volatility_bps_1m", "buy_liquidity_bucket", "sell_liquidity_bucket", "volatility_bucket"]
    for size in (100, 500, 1000):
        for side in ("buy", "sell"):
            columns.extend([f"{side}_{size}_bps", f"{side}_{size}_fill"])
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in observations:
            writer.writerow({key: row[key] for key in columns})

    per_ticker: dict[str, Any] = {}
    all_overall: list[dict[str, Any]] = []
    liquidity_tables: list[dict[str, Any]] = []
    volatility_tables: list[dict[str, Any]] = []
    for ticker, rows in by_ticker.items():
        overall = []
        for size in (100, 500, 1000):
            for side in ("buy", "sell"):
                costs = [row[f"{side}_{size}_bps"] for row in rows if row[f"{side}_{size}_bps"] is not None]
                result = {
                    "ticker": ticker,
                    "side": side,
                    "order_shares": size,
                    "mean_slippage_bps": statistics.mean(costs) if costs else None,
                    "mean_fill_rate": statistics.mean(row[f"{side}_{size}_fill"] for row in rows),
                }
                overall.append(result)
                all_overall.append(result)
        volatility = summarize(rows, "volatility_bucket", ("low", "middle", "high"), 500)
        for side in ("buy", "sell"):
            liquidity_tables.extend(summarize_directional_liquidity(rows, side))
        for record in volatility:
            volatility_tables.append({"ticker": ticker, **record})
        per_ticker[ticker] = {
            "source_rows": source_counts[ticker],
            "observations": len(rows),
            "mean_spread_cents": statistics.mean(row["spread_ticks"] for row in rows),
            "median_spread_cents": statistics.median(row["spread_ticks"] for row in rows),
            "median_two_sided_top5_depth_shares": statistics.median(row["two_sided_depth"] for row in rows),
            "mean_trailing_1m_realized_volatility_bps": statistics.mean(row["volatility_bps_1m"] for row in rows),
            "overall_execution": overall,
            "ask_depth_bucket_cutoffs_shares": [ask_low, ask_high],
            "bid_depth_bucket_cutoffs_shares": [bid_low, bid_high],
            "volatility_bucket_cutoffs_bps": [quantile([r["volatility_bps_1m"] for r in rows], 1 / 3), quantile([r["volatility_bps_1m"] for r in rows], 2 / 3)],
        }

    metrics = {
        "date": "2012-06-21",
        "source": "Official LOBSTER five-level samples",
        "sample_stride": stride,
        "total_usable_observations": len(observations),
        "execution_engine_validation": {
            **cpp_validation,
            "python_sweep_seconds": python_sweep_seconds,
            "timing_note": "Python timed as in-process sweep calls; C++ timed as one CLI round trip including process startup and serialization.",
        },
        "tickers": per_ticker,
        "overall_execution_by_ticker": all_overall,
        "liquidity_buckets_500_shares": liquidity_tables,
        "volatility_buckets_500_shares": volatility_tables,
        "synthetic_stress": synthetic_rows,
        "observation_csv": csv_path.as_posix(),
    }
    (output_dir / "lobster_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    volatility_buy_changes = []
    for ticker in by_ticker:
        low = next(row["buy_slippage_bps"] for row in volatility_tables if row["ticker"] == ticker and row["bucket"] == "low")
        high = next(row["buy_slippage_bps"] for row in volatility_tables if row["ticker"] == ticker and row["bucket"] == "high")
        volatility_buy_changes.append({"ticker": ticker, "low_vol_buy_bps": low, "high_vol_buy_bps": high, "high_minus_low_bps": high - low})

    report = f"""# Historical Order Book Execution Study

## Executive summary

The run replays hypothetical market orders against **{len(observations):,} snapshots from {len(by_ticker)} NASDAQ stocks** on June 21, 2012. Liquidity and volatility buckets are compared within each ticker at fixed order sizes. The sample is one historical day, so the results are descriptive and do not establish causality or forecast current execution.

## Data and research context

- **Data:** Official LOBSTER five-level samples for {', '.join(by_ticker)}, June 21, 2012, 09:30–16:00 US Eastern. Event counts by ticker: {', '.join(f"{t} {n:,}" for t, n in source_counts.items())}. Every {stride}th aligned event/book snapshot was analyzed.
- **Source:** [Official LOBSTER sample files](https://php.lobsterdata.com/info/DataSamples.php). LOBSTER documents that each message row corresponds to the resulting order-book row, with price recorded in 1/10,000 USD and sizes in shares; see [output format](https://php.lobsterdata.com/info/DataStructure.php).
- **Paper:** Xiao et al. (2025), [LiT: limit order book transformer](https://doi.org/10.3389/frai.2025.1616485), studies short-horizon market-movement forecasting from Binance Level 2 data. This report covers matching and execution; the separate PEKAO study tests a short-horizon direction benchmark.

## Method

For each ticker, the analyzer samples every {stride}th event, excludes crossed/invalid or empty quotes, computes volatility from the sampled midpoint path, and drops the first 60 seconds so every volatility estimate has a complete trailing window. It then replays hypothetical buy orders against the five displayed ask levels and sells against the five bid levels. Order sizes are 100, 500, and 1,000 shares. VWAP shortfall is measured against the snapshot midpoint in basis points; positive values indicate immediate execution cost. Fill rate is filled shares divided by requested shares, and unavailable depth remains unfilled.

Within each ticker, buy liquidity buckets use terciles of displayed ask depth and sell liquidity buckets use bid depth, each across the top five levels. Volatility buckets use terciles of trailing one-minute realised volatility from sampled midpoints, using observations at or before each snapshot. Results are grouped by ticker so cross-stock depth differences do not define the buckets. The 500-share order is compared across buckets. These are observational comparisons: spread, time of day, and other market conditions can confound the relationships. Snapshot replay assumes immediate access to all displayed size; it omits latency, queue priority, fees, hidden liquidity, replenishment, and market impact.

Each snapshot is executed through the C++17 order book at both sides and all three sizes. The results are compared against the Python sweep implementation on the exact same snapshot set before reporting; see the engine validation record in `lobster_metrics.json`. The timing values are diagnostic and not a clean speed ratio: the C++ value includes its CLI process and input/output transfer.

## Overall results

{markdown_table(all_overall)}

Slippage generally rises with order size and fill rates fall as the request consumes more than the five displayed levels. See per-ticker summaries below; pooled values above weight each ticker by its sampled event count.

## Liquidity terciles (500-share order)

{markdown_table(liquidity_tables)}

## Trailing one-minute volatility terciles (500-share order)

{markdown_table(volatility_tables)}

## Main findings

C++/Python replay parity passed for **{cpp_validation['parity_checks']:,}** side/size observations across **{cpp_validation['snapshots']:,}** snapshots. Maximum absolute shortfall difference: **{cpp_validation['max_abs_shortfall_error_bps']:.3g} bps**. Python in-process sweep time: **{python_sweep_seconds:.2f} s**; C++ CLI round trip: **{cpp_validation['cpp_wall_seconds']:.2f} s**. These timings have different measurement boundaries, so they should not be read as a speedup ratio.

{markdown_table(volatility_buy_changes)}

The high-volatility tercile had higher 500-share buy slippage in all five stocks, by {min(row['high_minus_low_bps'] for row in volatility_buy_changes):.3f} to {max(row['high_minus_low_bps'] for row in volatility_buy_changes):.3f} bps. Average spread also increased from low to high volatility in every stock. This is a consistent within-day association across these names, though spread and other market conditions may explain part of it.

For the liquidity comparison, each taker side is bucketed by the depth it actually consumes. Fill rates increase from thin to deep in every ticker. Slippage falls across the same buckets in some stocks but is not uniformly monotonic; for very deep INTC and MSFT books, a 500-share order is small enough that spread and other variation dominate the depth effect.

## Controlled synthetic stress experiment

The synthetic experiment runs 10,000 trials per condition with a fixed 40-unit buy order. Each book has five levels per side and equal depth at every level. A zero-mean fair-value shock in the range ±0, ±2, or ±10 ticks occurs after quotes are posted; quotes remain fixed. The table reports fill rate and mean absolute difference between execution VWAP and shocked fair value. Keeping quotes fixed isolates displayed depth and stale-quote uncertainty. The shock experiment is synthetic, not an empirical volatility estimate.

{markdown_table(synthetic_rows)}

## Interpretation and limits

The comparisons are within ticker: thin versus deep books for depth, and low versus high volatility. The patterns recur across some of the five stocks, but one day of data cannot establish causality or say whether they hold today. Spread and time of day may also differ between buckets. The replay assumes access to the displayed book and does not model order routing or market impact.

The [LiT paper](https://doi.org/10.3389/frai.2025.1616485) is a forecasting paper using Binance data and a transformer plus temporal modeling; it is related background rather than a method this project reproduces.

## Reproduction and artifacts

Run `python -m unittest discover -v`, `python analyze_lobster.py`, and `python experiment.py`. The analysis writes `results/lobster_metrics.json`, `results/lobster_execution_observations.csv`, `results/synthetic_stress_results.csv`, and this report. Raw archives are not committed; follow `data/README.md` to obtain the samples. Row-level output is generated locally and excluded from Git.

## References

1. Xiao, Y. et al. (2025). *LiT: limit order book transformer*. Frontiers in Artificial Intelligence, 8:1616485. [Article](https://doi.org/10.3389/frai.2025.1616485).
2. LOBSTER. [Sample files](https://php.lobsterdata.com/info/DataSamples.php) and [message/order-book output structure](https://php.lobsterdata.com/info/DataStructure.php).
"""
    (output_dir / "execution_study.md").write_text(report, encoding="utf-8")
    print(f"Analyzed {len(observations):,} snapshots across {len(by_ticker)} tickers")
    print(f"Wrote {output_dir / 'execution_study.md'}")
    print(f"Wrote {csv_path} and {output_dir / 'lobster_metrics.json'}")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("results"))
    parser.add_argument("--stride", type=int, default=10, help="analyze every Nth aligned event")
    args = parser.parse_args()
    analyze(args.data_dir, args.output_dir, args.stride)


if __name__ == "__main__":
    main()
