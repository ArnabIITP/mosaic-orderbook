"""Controlled stress experiment: fixed-size market buys across book conditions."""

from __future__ import annotations

import argparse
import csv
import random
import statistics
from pathlib import Path

from order_book import OrderBook


def run_case(depth_per_level: int, volatility: int, trials: int, seed: int, order_size: int) -> dict[str, float]:
    rng = random.Random(seed)
    slippages: list[float] = []
    fill_rates: list[float] = []

    for _ in range(trials):
        initial_mid = 10_000
        book = OrderBook()
        for level in range(1, 6):
            book.limit_order(f"ask-{level}", "sell", initial_mid + level, depth_per_level)
            book.limit_order(f"bid-{level}", "buy", initial_mid - level, depth_per_level)

        # A zero-mean reference-price shock occurs after quotes are posted.
        # The unchanged quotes represent stale liquidity at the execution time.
        arrival_mid = initial_mid + rng.randint(-volatility, volatility)
        _, unfilled = book.market_order("taker", "buy", order_size)
        filled = order_size - unfilled
        if filled:
            vwap = sum(t.price * t.quantity for t in book.trades) / filled
            slippages.append(abs(vwap - arrival_mid))
        fill_rates.append(filled / order_size)

    return {
        "mean_fill_rate": statistics.mean(fill_rates),
        "mean_abs_slippage_ticks": statistics.mean(slippages) if slippages else 0.0,
    }


def run_study(trials: int = 10_000, seed: int = 7, order_size: int = 40) -> list[dict[str, float | int]]:
    rows = []
    for depth in (2, 10, 50):
        for volatility in (0, 2, 10):
            result = run_case(depth, volatility, trials, seed + depth * 100 + volatility, order_size)
            rows.append({"depth_per_level": depth, "shock_half_range_ticks": volatility, "trials": trials, "order_size": order_size, **result})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--order-size", type=int, default=40)
    parser.add_argument("--output", type=Path, default=Path("results/synthetic_stress_results.csv"))
    args = parser.parse_args()
    if args.trials <= 0 or args.order_size <= 0:
        parser.error("--trials and --order-size must be positive")

    print(f"fixed buy order: {args.order_size} units; trials/cell: {args.trials}")
    print("depth/level volatility mean-fill-rate mean-absolute-slippage(ticks)")
    rows = run_study(args.trials, args.seed, args.order_size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(
            f"{row['depth_per_level']:>12} {row['shock_half_range_ticks']:>10}"
            f" {row['mean_fill_rate']:>15.1%} {row['mean_abs_slippage_ticks']:>31.3f}"
        )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
