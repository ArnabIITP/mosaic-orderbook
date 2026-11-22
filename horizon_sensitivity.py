"""Run the same chronological direction benchmark at several target horizons."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ml_direction_benchmark import ROOT, _date_partitions, run as run_direction


HORIZONS_SECONDS = (5.0, 10.0, 30.0, 60.0)


def _report(metrics: dict[str, Any]) -> str:
    lines = [
        "# PEKAO Direction Model Horizon Sensitivity", "",
        "## Question", "",
        "Does the held-out direction-classification result change when the same feature set and calendar-date split are evaluated at different forward horizons? This is a sensitivity study; no horizon is selected from its test score.", "",
        "## Protocol", "",
        f"The study uses {metrics['source_rows']:,} sampled snapshots from one PEKAO instrument in 2017. For each horizon, the target is the first same-session sampled midpoint at or after that horizon, with a maximum delay of twice the requested horizon. All horizons use the same chronological date blocks: {metrics['date_splits']['train']['count']} train dates, {metrics['date_splits']['validation']['count']} validation dates, and {metrics['date_splits']['test']['count']} final test dates. Models are refit independently on each horizon using the existing training-only preprocessing and fixed model settings.", "",
        "## Held-out results", "",
        "| Requested horizon | Labeled rows (% source) | Realized delay median / p95 | Test rows | Boosted-tree balanced accuracy | Lift vs majority (95% paired date CI) | Log-loss change vs majority |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in metrics["horizons"]:
        interval = item["balanced_accuracy_lift_ci95"]
        lift = f"{item['balanced_accuracy_lift_pp']:+.2f} pp [{interval[0]:+.2f}, {interval[1]:+.2f}]"
        lines.append(
            f"| {item['horizon_seconds']:g}s | {item['labeled_rows']:,} ({item['coverage_pct']:.1f}%) | "
            f"{item['realized_delay_seconds']['median']:.1f}s / {item['realized_delay_seconds']['p95']:.1f}s | "
            f"{item['test_rows']:,} | {item['boosted_balanced_accuracy']:.3f} | {lift} | "
            f"{item['log_loss_change_vs_majority']:+.3f} |"
        )
    lines.extend([
        "", "## Interpretation and limits", "",
        "Shorter horizons have much lower row coverage because snapshots are sampled every 100 source events. Those rows disproportionately come from periods with faster event activity; they are not a representative high-frequency sample. Realized target delay can materially exceed the requested horizon, as shown in the table. The 5-second and 10-second results therefore describe this selected sample, not a reliable seconds-level HFT forecast.", "",
        "A balanced-accuracy lift does not imply calibrated probabilities or positive net returns. The separate [execution-aware markout study](execution_aware_study.md) evaluates the 60-second calls after a displayed-book round trip and finds negative mean markout after the stated costs. This experiment does not evaluate passive order fills, queue position, a portfolio, or current market conditions.", "",
        "All date blocks, row counts, class counts, and per-model metrics are recorded in the companion JSON.", "",
    ])
    return "\n".join(lines)


def run(input_path: Path, output_json: Path, output_report: Path) -> dict[str, Any]:
    source = pd.read_csv(input_path, dtype={"date": str})
    source["date"] = source["date"].str.zfill(8)
    partitions = _date_partitions(sorted(source["date"].unique().tolist()))
    build_dir = ROOT / "build" / "horizon_sensitivity"
    build_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    full_metrics = {}
    for horizon in HORIZONS_SECONDS:
        label = f"{int(horizon)}s"
        one = run_direction(
            input_path,
            build_dir / f"{label}_metrics.json",
            ROOT / "results" / "wselob_2017_metrics.json",
            build_dir / f"{label}_figure.png",
            horizon,
            horizon * 2,
            ROOT / "results" / "execution_aware_metrics.json",
            partitions,
        )
        full_metrics[label] = one
        base = one["test_metrics"]["majority_baseline"]
        tree = one["test_metrics"]["hist_gradient_boosting"]
        delta_ci = one["date_bootstrap"]["delta_vs_majority_95ci"]["hist_gradient_boosting"]
        rows.append({
            "horizon_seconds": horizon,
            "labeled_rows": one["labeled_rows"],
            "coverage_pct": one["labeled_rows"] / one["source_rows"] * 100.0,
            "test_rows": one["split_rows"]["test"],
            "realized_delay_seconds": one["label_definition"]["realized_delay_seconds"],
            "class_counts": one["label_definition"]["class_counts"],
            "majority_balanced_accuracy": base["balanced_accuracy"],
            "boosted_balanced_accuracy": tree["balanced_accuracy"],
            "balanced_accuracy_lift_pp": (tree["balanced_accuracy"] - base["balanced_accuracy"]) * 100.0,
            "balanced_accuracy_lift_ci95": [value * 100.0 for value in delta_ci],
            "majority_log_loss": base["log_loss"],
            "boosted_log_loss": tree["log_loss"],
            "log_loss_change_vs_majority": tree["log_loss"] - base["log_loss"],
            "all_model_metrics": one["test_metrics"],
            "date_bootstrap": one["date_bootstrap"],
        })
    result = {
        "study": "PEKAO direction-model horizon sensitivity",
        "source_csv": input_path.name,
        "source_rows": int(len(source)),
        "date_splits": {name: {"first": days[0], "last": days[-1], "count": len(days)} for name, days in partitions.items()},
        "maximum_target_delay_rule": "twice requested horizon",
        "model_protocol": "same features, fixed model settings, training-only preprocessing, and chronological date blocks across horizons",
        "horizons": rows,
        "full_metrics_by_horizon": full_metrics,
        "interpretation": "Shorter horizon results have lower coverage and are selected toward faster-activity intervals; they are not broad HFT evidence.",
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    output_report.write_text(_report(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results" / "wselob_2017_execution_observations.csv")
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "horizon_sensitivity_metrics.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "horizon_sensitivity_study.md")
    args = parser.parse_args()
    result = run(args.input, args.output_json, args.output_report)
    print(json.dumps({"date_splits": result["date_splits"], "horizons": result["horizons"]}, indent=2))


if __name__ == "__main__":
    main()
