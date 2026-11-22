"""Evaluate whether direction-model calls survive a displayed-book round trip."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml_direction_benchmark import FEATURES, _date_partitions, _with_features_and_labels


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "results" / "wselob_2017_execution_observations.csv"
HORIZON_SECONDS = 60.0
MAX_DELAY_SECONDS = 120.0
ORDER_SIZE = 500
FEE_BPS_PER_SIDE = 1.0
BOOTSTRAP_REPS = 2000


def _attach_future_book(data: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    """Join each eligible row to its first same-session quote at the target horizon."""
    pieces = []
    for date, rows in data.groupby("date", sort=True):
        book = source.loc[source["date"] == date].sort_values("timestamp", kind="stable")
        timestamps = book["timestamp"].to_numpy(dtype=float)
        targets = np.searchsorted(timestamps, rows["timestamp"].to_numpy(dtype=float) + HORIZON_SECONDS, side="left")
        valid = targets < len(book)
        safe_targets = np.minimum(targets, len(book) - 1)
        delays = timestamps[safe_targets] - rows["timestamp"].to_numpy(dtype=float)
        valid &= delays <= MAX_DELAY_SECONDS
        selected = rows.loc[valid].copy()
        positions = targets[valid]
        future = book.iloc[positions]
        selected["future_mid"] = future["mid"].to_numpy(dtype=float)
        selected["future_buy_bps"] = future[f"buy_{ORDER_SIZE}_bps"].to_numpy(dtype=float)
        selected["future_sell_bps"] = future[f"sell_{ORDER_SIZE}_bps"].to_numpy(dtype=float)
        selected["future_buy_fill"] = future[f"buy_{ORDER_SIZE}_fill"].to_numpy(dtype=float)
        selected["future_sell_fill"] = future[f"sell_{ORDER_SIZE}_fill"].to_numpy(dtype=float)
        selected["target_delay_seconds"] = delays[valid]
        pieces.append(selected)
    return pd.concat(pieces, ignore_index=True)


def _trade_outcomes(rows: pd.DataFrame, predictions: np.ndarray, fee_bps: float) -> dict[str, Any]:
    """Return long/short hypothetical round-trip markout per initial-mid notional."""
    valid = (
        (rows[f"buy_{ORDER_SIZE}_fill"].to_numpy(dtype=float) >= 1.0)
        & (rows[f"sell_{ORDER_SIZE}_fill"].to_numpy(dtype=float) >= 1.0)
        & (rows["future_buy_fill"].to_numpy(dtype=float) >= 1.0)
        & (rows["future_sell_fill"].to_numpy(dtype=float) >= 1.0)
    )
    side = np.where(predictions == "up", 1, np.where(predictions == "down", -1, 0))
    traded = valid & (side != 0)
    mid = rows["mid"].to_numpy(dtype=float)
    future_mid = rows["future_mid"].to_numpy(dtype=float)
    buy_entry = rows[f"buy_{ORDER_SIZE}_bps"].to_numpy(dtype=float)
    sell_entry = rows[f"sell_{ORDER_SIZE}_bps"].to_numpy(dtype=float)
    buy_exit = rows["future_buy_bps"].to_numpy(dtype=float)
    sell_exit = rows["future_sell_bps"].to_numpy(dtype=float)

    # Prices are reconstructed from each snapshot's 500-share sweep VWAP.
    long_net = ((future_mid * (1.0 - sell_exit / 10_000.0) / mid) - (1.0 + buy_entry / 10_000.0)) * 10_000.0
    short_net = ((1.0 - sell_entry / 10_000.0) - future_mid * (1.0 + buy_exit / 10_000.0) / mid) * 10_000.0
    net = np.where(side > 0, long_net, short_net) - (2.0 * fee_bps)
    selected = rows.loc[traded, ["date"]].copy()
    selected["net_bps"] = net[traded]
    daily = selected.groupby("date")["net_bps"].agg(["sum", "count"])
    observed = selected["net_bps"].to_numpy(dtype=float)
    if len(observed):
        rng = np.random.default_rng(20261122)
        totals = daily["sum"].to_numpy(dtype=float)
        counts = daily["count"].to_numpy(dtype=float)
        bootstrap_means = np.empty(BOOTSTRAP_REPS, dtype=float)
        for i in range(BOOTSTRAP_REPS):
            sample = rng.integers(0, len(daily), size=len(daily))
            bootstrap_means[i] = totals[sample].sum() / counts[sample].sum()
        interval = [float(v) for v in np.quantile(bootstrap_means, [0.025, 0.975])]
        mean_daily = float((totals / counts).mean())
    else:
        interval = [None, None]
        mean_daily = None
    return {
        "signals": int(traded.sum()),
        "signal_rate_pct": float(traded.mean() * 100.0),
        "active_test_dates": int(len(daily)),
        "mean_net_markout_bps_per_trade": float(observed.mean()) if len(observed) else None,
        "trade_weighted_date_bootstrap_95ci_bps": interval,
        "mean_of_active_date_means_bps": mean_daily,
        "daily_mean_net_markout_bps": {
            str(date): float(row_sum / row_count)
            for date, row_sum, row_count in zip(daily.index, daily["sum"], daily["count"])
        },
        "median_net_markout_bps": float(np.median(observed)) if len(observed) else None,
        "profitable_trade_rate_pct": float((observed > 0).mean() * 100.0) if len(observed) else None,
        "fees_bps_per_side": fee_bps,
        "round_trip_shares": ORDER_SIZE,
    }


def _render_report(metrics: dict[str, Any]) -> str:
    lines = [
        "# PEKAO Execution-Aware Direction Benchmark", "",
        "## Question", "",
        "When the direction classifier calls the next sampled midpoint up or down, does that call remain favorable after crossing the displayed book to enter and exit a 500-share position? This is a conditional markout diagnostic, not a portfolio backtest.", "",
        "## Data and protocol", "",
        f"The experiment uses {metrics['eligible_rows']:,} eligible snapshots from {metrics['source_rows']:,} source rows. The first same-session quote at or after 60 seconds is used if it arrives within 120 seconds; realized delay is {metrics['target']['realized_delay_seconds']['median']:.1f} seconds median and {metrics['target']['realized_delay_seconds']['p95']:.1f} seconds at the 95th percentile. The final 15% of trading dates are held out. Models are trained only on the earlier 70% of dates; the middle 15% is reserved for validation and does not tune this fixed comparison.", "",
        "For each test snapshot, a hypothetical 500-share market order enters at the displayed buy or sell sweep VWAP. At the target snapshot, it exits with the opposite-side sweep VWAP. The calculation requires a full displayed 500-share fill on both sides at both snapshots and deducts the stated fee on entry and exit. Costs and future prices are normalized to the decision midpoint.", "",
        "## Held-out outcomes", "",
        "| Signal rule | Calls (up/down/flat) | Active dates | Mean net markout (bps/trade) | 95% date-bootstrap interval | Profitable trades |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in metrics["signal_rules"].items():
        row = metrics["markout_metrics"][key]
        ci = row["trade_weighted_date_bootstrap_95ci_bps"]
        ci_text = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci[0] is not None else "n/a"
        mean_text = f"{row['mean_net_markout_bps_per_trade']:.2f}" if row["mean_net_markout_bps_per_trade"] is not None else "n/a"
        win_text = f"{row['profitable_trade_rate_pct']:.1f}%" if row["profitable_trade_rate_pct"] is not None else "n/a"
        counts = metrics["predicted_class_counts"][key]
        calls = f"{counts['up']:,}/{counts['down']:,}/{counts['flat']:,}"
        lines.append(f"| {label} | {calls} | {row['active_test_dates']} | {mean_text} | {ci_text} | {win_text} |")
    lines.extend([
        "", f"The boosted tree improves the point estimate over the majority rule by {metrics['paired_model_difference']['mean_daily_difference_bps']:.2f} bps per date; its paired date-bootstrap 95% interval is [{metrics['paired_model_difference']['ci95_low_bps']:.2f}, {metrics['paired_model_difference']['ci95_high_bps']:.2f}]. All classifier predictions were directional on this test block (none predicted flat), so this is a full-coverage diagnostic rather than a selective signal policy.", "",
        "The rule set includes the prior-only majority classifier, logistic regression, histogram gradient boosting, and a simple top-book depth-imbalance sign rule. The models use the same nine backward-looking features and chronological date split as the direction benchmark. The bootstrap resamples whole active test dates and weights by the number of signals on each sampled date.", "",
        "## Interpretation and limits", "",
        "All four rules have negative mean net markout after the stated fees. Signal windows overlap, so these rows are not independent trades and cannot be summed into portfolio returns. The study assumes immediate complete fills at static displayed depth, does not model queue position or intervening book changes, and uses an illustrative 1 bp fee per side. The sample is one stock in one historical year; quotes are sampled every 100 source events, making this unsuitable for claiming seconds-level HFT performance. No capital, inventory, or risk limits are simulated.", "",
        "The intended result is a cost-aware quality check on the direction predictions, not a claim of profitability. See the JSON metrics for source hash, date splits, coverage and model details.", "",
    ])
    return "\n".join(lines)


def run(input_path: Path, output_json: Path, output_report: Path, fee_bps: float) -> dict[str, Any]:
    source = pd.read_csv(input_path, dtype={"date": str})
    source["date"] = source["date"].str.zfill(8)
    labeled = _with_features_and_labels(source, HORIZON_SECONDS, MAX_DELAY_SECONDS)
    data = _attach_future_book(labeled, source)
    dates = sorted(data["date"].unique().tolist())
    partitions = _date_partitions(dates)
    train = data.loc[data["date"].isin(partitions["train"])].copy()
    validation_dates = set(partitions["validation"])
    test = data.loc[data["date"].isin(partitions["test"])].copy()
    X_train = train.loc[:, FEATURES].replace([np.inf, -np.inf], np.nan)
    y_train = train["label"].astype(str).to_numpy()
    X_test = test.loc[:, FEATURES].replace([np.inf, -np.inf], np.nan)
    models = {
        "logistic_regression": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=1000, random_state=17)),
        "hist_gradient_boosting": make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(learning_rate=0.05, max_iter=200, max_leaf_nodes=15, l2_regularization=1.0, random_state=17)),
    }
    predictions: dict[str, np.ndarray] = {}
    predictions["majority_baseline"] = np.full(len(test), pd.Series(y_train).mode().iloc[0], dtype=object)
    for name, model in models.items():
        model.fit(X_train, y_train)
        predictions[name] = model.predict(X_test)
    imbalance = test["depth_imbalance"].to_numpy(dtype=float)
    predictions["depth_imbalance_sign"] = np.where(imbalance > 0, "up", np.where(imbalance < 0, "down", "flat"))
    rules = {
        "majority_baseline": "Training-prior majority class",
        "logistic_regression": "Logistic regression",
        "hist_gradient_boosting": "Histogram gradient boosting",
        "depth_imbalance_sign": "Depth-imbalance sign",
    }
    markouts = {name: _trade_outcomes(test, prediction, fee_bps) for name, prediction in predictions.items()}
    common_dates = sorted(set(markouts["majority_baseline"]["daily_mean_net_markout_bps"])
                          & set(markouts["hist_gradient_boosting"]["daily_mean_net_markout_bps"]))
    paired_differences = np.array([
        markouts["hist_gradient_boosting"]["daily_mean_net_markout_bps"][date]
        - markouts["majority_baseline"]["daily_mean_net_markout_bps"][date]
        for date in common_dates
    ], dtype=float)
    paired_rng = np.random.default_rng(20261123)
    paired_bootstrap = paired_differences[paired_rng.integers(
        0, len(paired_differences), size=(BOOTSTRAP_REPS, len(paired_differences))
    )].mean(axis=1)
    paired_delta = {
        "comparison": "histogram gradient boosting minus training-prior majority",
        "dates": len(common_dates),
        "mean_daily_difference_bps": float(paired_differences.mean()),
        "ci95_low_bps": float(np.quantile(paired_bootstrap, .025)),
        "ci95_high_bps": float(np.quantile(paired_bootstrap, .975)),
        "bootstrap_method": "paired date-cluster bootstrap of per-date mean markout differences",
    }
    fillable = (
        (test[f"buy_{ORDER_SIZE}_fill"].to_numpy(dtype=float) >= 1.0)
        & (test[f"sell_{ORDER_SIZE}_fill"].to_numpy(dtype=float) >= 1.0)
        & (test["future_buy_fill"].to_numpy(dtype=float) >= 1.0)
        & (test["future_sell_fill"].to_numpy(dtype=float) >= 1.0)
    )
    metrics: dict[str, Any] = {
        "study": "PEKAO execution-aware directional markout diagnostic",
        "source_csv": input_path.name,
        "source_csv_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "source_rows": int(len(source)),
        "eligible_rows": int(len(data)),
        "test_rows": int(len(test)),
        "test_fully_fillable_rows": int(fillable.sum()),
        "full_fill_coverage_pct": float(fillable.mean() * 100.0),
        "target": {"horizon_seconds": HORIZON_SECONDS, "maximum_delay_seconds": MAX_DELAY_SECONDS, "realized_delay_seconds": {"median": float(data["target_delay_seconds"].median()), "p95": float(data["target_delay_seconds"].quantile(.95))}},
        "split_dates": {name: {"first": group[0], "last": group[-1], "count": len(group)} for name, group in partitions.items()},
        "split_rows": {"train": int(len(train)), "validation": int(data["date"].isin(validation_dates).sum()), "test": int(len(test))},
        "features": list(FEATURES),
        "order_size_shares": ORDER_SIZE,
        "fee_bps_per_side": fee_bps,
        "markout_metrics": markouts,
        "paired_model_difference": paired_delta,
        "predicted_class_counts": {name: {label: int(np.sum(prediction == label)) for label in ("up", "down", "flat")}
                                   for name, prediction in predictions.items()},
        "signal_rules": rules,
        "bootstrap": {"replicates": BOOTSTRAP_REPS, "method": "date-cluster bootstrap of trade-weighted mean net markout; seed 20261122"},
        "interpretation": "Conditional, overlapping hypothetical round-trip markouts; not a portfolio backtest or evidence of a deployable strategy.",
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(_render_report(metrics), encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "execution_aware_metrics.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "execution_aware_study.md")
    parser.add_argument("--fee-bps-per-side", type=float, default=FEE_BPS_PER_SIDE)
    args = parser.parse_args()
    result = run(args.input, args.output_json, args.output_report, args.fee_bps_per_side)
    print(json.dumps({"target": result["target"], "split_rows": result["split_rows"], "full_fill_coverage_pct": result["full_fill_coverage_pct"], "markout_metrics": result["markout_metrics"], "outputs": [str(args.output_json), str(args.output_report)]}, indent=2))


if __name__ == "__main__":
    main()
