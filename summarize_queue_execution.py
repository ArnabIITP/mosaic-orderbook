"""Combine queue execution scenario outputs into a recruiter-facing table."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CASES = {
    "500 shares, immediate": ROOT / "results" / "queue_execution_metrics.json",
    "100 shares, immediate": ROOT / "results" / "queue_execution_100share_metrics.json",
    "500 shares, 100 ms arrival": ROOT / "results" / "queue_execution_latency100ms_metrics.json",
}


def _metric(data: dict[str, Any], key: str) -> dict[str, Any]:
    return data["results"][key]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "queue_execution_sensitivity.md")
    args = parser.parse_args()
    cases = [(label, json.loads(path.read_text(encoding="utf-8"))) for label, path in CASES.items()]
    lines = [
        "# Queue Execution and Policy Sensitivity", "",
        "Each scenario replays the same 383 held-out directional PEKAO calls. Passive orders join the displayed best quote behind visible FIFO size; the marketable baseline sweeps displayed depth at entry and at the target. Bps are normalized to requested initial notional, with unfilled passive quantity contributing zero.", "",
        "| Scenario | Policy / M-decrease treatment | Fill probability | Mean fill fraction | Mean net bps per requested notional | Date-bootstrap 95% interval |", "|---|---|---:|---:|---:|---:|",
    ]
    policy_names = [("passive_execution_proxy", "Passive; M decreases as execution proxy"),
                    ("passive_cancellation", "Passive; M decreases as cancellation"),
                    ("marketable_taker", "Marketable entry and exit")]
    for label, data in cases:
        for key, policy in policy_names:
            row = _metric(data, key)
            ci = row["requested_notional_date_bootstrap_95ci_bps"]
            ci_text = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci[0] is not None else "n/a"
            lines.append(f"| {label} | {policy} | {row['fill_probability_pct']:.1f}% | {row['mean_fill_fraction_pct']:.1f}% | {row['mean_round_trip_net_bps_on_requested_notional']:.2f} | {ci_text} |")
    lines.extend([
        "", "## Read this result carefully", "",
        "Passive fill estimates depend on an ambiguous source message. WSELOB `M` updates can encode modifications or partial fills, but there is no independent trade-print stream. Treating every same-price reduction as execution gives a sensitivity scenario; treating reductions as cancellations gives a no-fill conservative case. The apparent positive midpoint markout among fills does not survive as positive P&L after a displayed-book exit and taker fee.", "",
        "The marketable comparison is not a production simulator: both policies use static reconstructed depth, assume immediate access at sampled event times, and omit market impact, replenishment, hidden liquidity, order interaction, inventory constraints, and risk controls. Signal horizons overlap. The bootstrap resamples whole dates, but this selected-call study is still diagnostic rather than a portfolio backtest.", "",
        "See [the protocol and detailed assumptions](queue_execution_study.md). Reproduce the runs using its commands.", "",
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
