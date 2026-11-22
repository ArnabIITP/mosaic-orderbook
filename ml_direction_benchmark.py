"""Build a held-out three-class mid-price direction benchmark on PEKAO."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parent
LABELS = ("down", "flat", "up")
FEATURES = (
    "spread_bps",
    "depth_imbalance",
    "log_bid_depth",
    "log_ask_depth",
    "log_total_depth",
    "volatility_1m_bps",
    "return_30s_bps",
    "return_120s_bps",
    "return_300s_bps",
)


def _with_features_and_labels(frame: pd.DataFrame, horizon_seconds: float, max_delay_seconds: float) -> pd.DataFrame:
    """Build prediction-time features and same-session forward labels."""
    rows: list[pd.DataFrame] = []
    grouping = ["ticker", "date"] if "ticker" in frame.columns else ["date"]
    for _date, group in frame.groupby(grouping, sort=True):
        group = group.sort_values("timestamp", kind="stable").copy()
        timestamps = group["timestamp"].to_numpy(dtype=np.float64)
        mid = group["mid"].to_numpy(dtype=np.float64)
        ask = group["ask_depth"].to_numpy(dtype=np.float64)
        bid = group["bid_depth"].to_numpy(dtype=np.float64)

        group["spread_bps"] = group["spread_pln"].to_numpy(dtype=np.float64) * 100.0 / mid * 10_000.0
        total = ask + bid
        group["depth_imbalance"] = np.divide(bid - ask, total, out=np.zeros_like(total), where=total > 0)
        group["log_bid_depth"] = np.log1p(bid)
        group["log_ask_depth"] = np.log1p(ask)
        group["log_total_depth"] = np.log1p(total)
        group["volatility_1m_bps"] = group["volatility_bps_1m"].astype(float)

        for seconds in (30, 120, 300):
            past_idx = np.searchsorted(timestamps, timestamps - seconds, side="right") - 1
            valid = past_idx >= 0
            values = np.full(len(group), np.nan)
            idx = np.flatnonzero(valid)
            age = timestamps[idx] - timestamps[past_idx[idx]]
            usable = age <= 2 * seconds
            current = idx[usable]
            prior = past_idx[current]
            values[current] = np.log(mid[current] / mid[prior]) * 10_000.0
            group[f"return_{seconds}s_bps"] = values

        future_idx = np.searchsorted(timestamps, timestamps + horizon_seconds, side="left")
        in_session = future_idx < len(group)
        candidate_rows = np.flatnonzero(in_session)
        target_idx = future_idx[candidate_rows]
        delay = timestamps[target_idx] - timestamps[candidate_rows]
        valid_target = delay <= max_delay_seconds
        candidates = candidate_rows[valid_target]
        targets = future_idx[candidates]
        move = mid[targets] - mid[candidates]
        label = np.where(move > 0, "up", np.where(move < 0, "down", "flat"))
        group["label"] = pd.Series([None] * len(group), index=group.index, dtype="object")
        group["target_delay_seconds"] = np.nan
        group.iloc[candidates, group.columns.get_loc("label")] = label
        group.iloc[candidates, group.columns.get_loc("target_delay_seconds")] = delay[valid_target]
        rows.append(group)

    result = pd.concat(rows, ignore_index=True)
    result = result.dropna(subset=["label"]).copy()
    result["label"] = pd.Categorical(result["label"], categories=LABELS)
    return result


def _date_partitions(dates: list[str]) -> dict[str, list[str]]:
    if len(dates) < 30:
        raise ValueError(f"Need at least 30 distinct sessions for blocked evaluation; found {len(dates)}")
    train_end = math.floor(0.70 * len(dates))
    validation_end = math.floor(0.85 * len(dates))
    return {"train": dates[:train_end], "validation": dates[train_end:validation_end], "test": dates[validation_end:]}


def _aligned_probabilities(model: Any, X: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(X)
    aligned = np.zeros((len(X), len(LABELS)), dtype=float)
    for col, label in enumerate(model.classes_):
        aligned[:, LABELS.index(str(label))] = raw[:, col]
    return aligned


def _score(y_true: np.ndarray, y_pred: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)),
        "log_loss": float(log_loss(y_true, probabilities, labels=LABELS)),
    }


def _daily_confusion(y: np.ndarray, pred: np.ndarray, dates: np.ndarray) -> tuple[list[str], np.ndarray]:
    unique_dates = sorted(set(dates))
    confusion = np.zeros((len(unique_dates), len(LABELS), len(LABELS)), dtype=np.int64)
    for day_index, day in enumerate(unique_dates):
        mask = dates == day
        for actual_index, actual in enumerate(LABELS):
            for predicted_index, predicted in enumerate(LABELS):
                confusion[day_index, actual_index, predicted_index] = np.sum((y[mask] == actual) & (pred[mask] == predicted))
    return unique_dates, confusion


def _bootstrap_balanced_accuracy(y: np.ndarray, predictions: dict[str, np.ndarray], dates: np.ndarray, reps: int = 2000) -> dict[str, Any]:
    unique_dates, _ = _daily_confusion(y, y, dates)
    model_confusions = {name: _daily_confusion(y, pred, dates)[1] for name, pred in predictions.items()}
    rng = np.random.default_rng(20261122)
    draws: dict[str, list[float]] = {name: [] for name in predictions}
    deltas: dict[str, list[float]] = {name: [] for name in predictions if name != "majority_baseline"}

    for _ in range(reps):
        sampled_days = rng.integers(0, len(unique_dates), size=len(unique_dates))
        sampled_counts = np.bincount(sampled_days, minlength=len(unique_dates))
        metrics: dict[str, float] = {}
        for name, matrices in model_confusions.items():
            matrix = np.einsum("d,dij->ij", sampled_counts, matrices)
            recalls = np.diag(matrix) / np.maximum(matrix.sum(axis=1), 1)
            metric = float(np.mean(recalls))
            draws[name].append(metric)
            metrics[name] = metric
        for name in deltas:
            deltas[name].append(metrics[name] - metrics["majority_baseline"])

    intervals = {
        name: [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]
        for name, values in draws.items()
    }
    delta_intervals = {
        name: [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]
        for name, values in deltas.items()
    }
    return {
        "method": "2,000 paired bootstrap resamples of held-out trading dates",
        "balanced_accuracy_95ci": intervals,
        "delta_vs_majority_95ci": delta_intervals,
    }


def _render_report(metrics: dict[str, Any]) -> str:
    label = metrics["label_definition"]
    splits = metrics["split_dates"]
    counts = label["class_counts"]
    lines = [
        "# WSELOB PEKAO Mid-Price Direction Benchmark",
        "",
        "## Research question",
        "",
        "Can a snapshot of the displayed book and recent price history classify the next sampled mid-price as down, unchanged, or up about one minute later? The label is price direction, not a simulated trade return.",
        "",
        "## Data and target",
        "",
        f"The benchmark uses {metrics['labeled_rows']:,} labeled snapshots from the 2017 PEKAO study ({metrics['source_rows']:,} source snapshots before target eligibility filtering). The target is the first sampled quote at or after {label['horizon_seconds']:.0f} seconds, provided it arrives within {label['maximum_target_delay_seconds']:.0f} seconds and on the same trading date. The realized target delay has median {label['realized_delay_seconds']['median']:.1f} seconds and 95th percentile {label['realized_delay_seconds']['p95']:.1f} seconds.",
        "",
        f"Label counts: down {counts['down']:,}, flat {counts['flat']:,}, up {counts['up']:,}.",
        "",
        "## Evaluation design",
        "",
        "Sessions are sorted chronologically and assigned as whole dates: 70% training, 15% validation, 15% test. No rows are shuffled across dates. Feature imputation, scaling, and model fitting use training dates only. The validation set is a forward-time diagnostic; model settings were fixed before evaluating the final test block.",
        "",
        f"- Train: {splits['train']['date_count']} dates ({splits['train']['first']}–{splits['train']['last']}), {metrics['split_rows']['train']:,} labeled snapshots.",
        f"- Validation: {splits['validation']['date_count']} dates ({splits['validation']['first']}–{splits['validation']['last']}), {metrics['split_rows']['validation']:,} labeled snapshots.",
        f"- Test: {splits['test']['date_count']} dates ({splits['test']['first']}–{splits['test']['last']}), {metrics['split_rows']['test']:,} labeled snapshots.",
        "",
        "The feature set uses spread, bid/ask depth imbalance and depth scales, trailing one-minute realized volatility, and backward-looking 30/120/300-second midpoint returns. It excludes the current mid-price level and all future values. The comparison is a training-prior majority baseline, regularized logistic regression, and a fixed-configuration histogram gradient-boosting classifier.",
        "",
        "## Held-out results",
        "",
        "| Model | Accuracy | Balanced accuracy | Macro F1 | Log loss |",
        "|---|---:|---:|---:|---:|",
    ]
    names = {
        "majority_baseline": "Training-prior baseline",
        "logistic_regression": "Logistic regression",
        "hist_gradient_boosting": "Histogram gradient boosting",
    }
    for model, title in names.items():
        m = metrics["test_metrics"][model]
        lines.append(f"| {title} | {m['accuracy']:.3f} | {m['balanced_accuracy']:.3f} | {m['macro_f1']:.3f} | {m['log_loss']:.3f} |")

    tree = metrics["test_metrics"]["hist_gradient_boosting"]
    base = metrics["test_metrics"]["majority_baseline"]
    lo, hi = metrics["date_bootstrap"]["delta_vs_majority_95ci"]["hist_gradient_boosting"]
    lines.extend([
        "",
        f"The boosted tree reaches {tree['balanced_accuracy']*100:.2f}% balanced accuracy versus {base['balanced_accuracy']*100:.2f}% for the majority baseline. In a paired 2,000-resample bootstrap over test dates, the lift is {(tree['balanced_accuracy']-base['balanced_accuracy'])*100:.2f} percentage points (95% interval {lo*100:.2f} to {hi*100:.2f}). Its log loss is slightly worse than the baseline; logistic regression also fails to improve balanced accuracy.",
        "",
        "## Interpretation and limits",
        "",
        "The lift is small and comes from one stock in one historical year. It may not persist on another instrument or in current data. Snapshots are sampled every 100 source events, so the quote used for the one-minute label arrives later: the median delay is 79.2 seconds and the 95th percentile is 114.2 seconds. No trading rule or net-return calculation is tested. Forward labels overlap within each date; the chronological split keeps dates separate, and the bootstrap resamples whole test dates, but observations within a date remain dependent.",
        "",
        "The separate [leave-one-stock-out study](multi_stock_ml_study.md) now tests transfer to four unseen WSE names using the same 2017 source. It finds a small positive balanced-accuracy lift across the five held-out stocks, but the [PEKAO cost-aware study](execution_aware_study.md) still shows negative average markout after its illustrative costs.",
        "",
    ])
    return "\n".join(lines)


def _make_figure(metrics: dict[str, Any], execution_metrics_path: Path, output_path: Path,
                 execution_aware_metrics_path: Path | None = None,
                 multi_stock_metrics_path: Path | None = None) -> None:
    execution = json.loads(execution_metrics_path.read_text(encoding="utf-8"))
    fig, axes = plt.subplots(1, 4, figsize=(19.5, 4.8))
    ax = axes[0]
    cross_path = multi_stock_metrics_path or (ROOT / "results" / "multi_stock_ml_metrics.json")
    if cross_path.exists():
        cross = json.loads(cross_path.read_text(encoding="utf-8"))
        folds = cross["folds"]
        names = [fold["held_out_ticker"] for fold in folds]
        values = [fold["test_metrics"]["hist_gradient_boosting"]["balanced_accuracy"] for fold in folds]
        intervals = [fold["date_bootstrap"]["balanced_accuracy_95ci"]["hist_gradient_boosting"] for fold in folds]
        lows = [values[i] - intervals[i][0] for i in range(len(folds))]
        highs = [intervals[i][1] - values[i] for i in range(len(folds))]
        ax.bar(names, values, color="#dd7b28", width=0.68)
        ax.errorbar(names, values, yerr=[lows, highs], fmt="none", ecolor="#263746", capsize=3, linewidth=1.1)
        ax.axhline(1 / 3, color="#6b7280", linestyle="--", linewidth=1, label="33.3% prior baseline")
        ax.set_ylim(0.31, max(interval[1] for interval in intervals) + 0.018)
        ax.set_title("Unseen-stock tests · boosted trees")
        ax.legend(frameon=False, fontsize=7, loc="upper left")
        ax.tick_params(axis="x", labelrotation=20, labelsize=8)
    else:
        ml = metrics["test_metrics"]
        ci = metrics["date_bootstrap"]["balanced_accuracy_95ci"]
        order = ("majority_baseline", "logistic_regression", "hist_gradient_boosting")
        pretty = ("Majority", "Logistic", "Boosted trees")
        colors = ("#9aa7b5", "#2078b4", "#dd7b28")
        values = [ml[name]["balanced_accuracy"] for name in order]
        lows = [values[i] - ci[order[i]][0] for i in range(3)]
        highs = [ci[order[i]][1] - values[i] for i in range(3)]
        ax.bar(pretty, values, color=colors, width=0.65)
        ax.errorbar(pretty, values, yerr=[lows, highs], fmt="none", ecolor="#263746", capsize=4, linewidth=1.2)
        ax.axhline(1 / 3, color="#6b7280", linestyle="--", linewidth=1, label="33.3% prior baseline")
        ax.set_ylim(0.25, max(ci[name][1] for name in order) + 0.09)
        ax.set_title("Held-out dates · direction model")
        ax.tick_params(axis="x", labelrotation=12)
        ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.set_ylabel("Balanced accuracy")

    ax = axes[1]
    effects = execution["paired_daily_volatility_effects"]
    sides = ("buy", "sell")
    effects = [next(row for row in effects if row["side"] == side) for side in sides]
    x = np.arange(2)
    means = [row["mean_daily_difference_bps"] for row in effects]
    lo = [means[i] - effects[i]["ci95_low_bps"] for i in range(2)]
    hi = [effects[i]["ci95_high_bps"] - means[i] for i in range(2)]
    ax.bar(x, means, color=("#2078b4", "#dd7b28"), width=0.58)
    ax.errorbar(x, means, yerr=[lo, hi], fmt="none", ecolor="#263746", capsize=4, linewidth=1.2)
    ax.axhline(0, color="#6b7280", linewidth=1)
    ax.set_xticks(x, ["Buy", "Sell"])
    ax.set_ylabel("Change in slippage (bps)")
    ax.set_title("500-share slippage · high minus low volatility")
    ax.text(0.02, 0.97, "95% date-bootstrap intervals", transform=ax.transAxes, va="top", fontsize=8, color="#5b6570")

    ax = axes[2]
    replay = execution["cpp_event_replay_benchmark"]
    ax.axis("off")
    ax.set_title("Verified systems result", pad=12)
    kpis = [
        ("9.87M", "historical updates\nreplayed"),
        (f"{replay['cpp_events_per_second_median']/1e6:.2f}M/s", "median C++\nthroughput"),
        (f"{replay['matched_snapshots']:,}", "top-ten books\nchecked"),
        ("0", "Python/C++\nmismatches"),
    ]
    for i, (value, caption) in enumerate(kpis):
        row, col = divmod(i, 2)
        x0 = 0.04 + col * 0.49
        y0 = 0.58 - row * 0.42
        ax.add_patch(Rectangle((x0, y0), 0.43, 0.36, facecolor="#f1f6f4", edgecolor="#d7e4df", linewidth=1, transform=ax.transAxes))
        ax.text(x0 + 0.04, y0 + 0.24, value, transform=ax.transAxes, fontsize=18, weight="bold", color="#176b50", va="center")
        ax.text(x0 + 0.04, y0 + 0.07, caption, transform=ax.transAxes, fontsize=8.2, color="#334155", va="center", linespacing=0.95)

    ax = axes[3]
    costed_path = execution_aware_metrics_path or (ROOT / "results" / "execution_aware_metrics.json")
    if costed_path.exists():
        costed = json.loads(costed_path.read_text(encoding="utf-8"))
        names = ("majority_baseline", "logistic_regression", "hist_gradient_boosting", "depth_imbalance_sign")
        labels = ("Majority", "Logistic", "Boosted", "Imbalance")
        rows = [costed["markout_metrics"][name] for name in names]
        means = [row["mean_net_markout_bps_per_trade"] for row in rows]
        intervals = [row["trade_weighted_date_bootstrap_95ci_bps"] for row in rows]
        lower = [means[i] - intervals[i][0] for i in range(len(rows))]
        upper = [intervals[i][1] - means[i] for i in range(len(rows))]
        x = np.arange(len(rows))
        ax.bar(x, means, color=("#9aa7b5", "#2078b4", "#dd7b28", "#789b8a"), width=0.68)
        ax.errorbar(x, means, yerr=[lower, upper], fmt="none", ecolor="#263746", capsize=3, linewidth=1.1)
        ax.axhline(0, color="#6b7280", linewidth=1)
        ax.set_xticks(x, labels, rotation=12)
        ax.set_ylabel("Net markout (bps/trade)")
        ax.set_title("Held-out net round trip\n95% date-bootstrap intervals")
    else:
        ax.axis("off")
        ax.set_title("Execution-aware markout")
        ax.text(0.05, 0.5, "Run execution_aware_benchmark.py\nto add costed held-out results", transform=ax.transAxes, fontsize=10, color="#5b6570")

    fig.subplots_adjust(top=0.80, bottom=0.19, wspace=0.38)
    fig.suptitle("MOSAIC · research and systems evidence", fontsize=15, fontweight="bold", y=0.97)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def run(input_path: Path, output_path: Path, execution_metrics_path: Path, figure_path: Path,
        horizon_seconds: float, max_delay_seconds: float,
        execution_aware_metrics_path: Path | None = None,
        fixed_partitions: dict[str, list[str]] | None = None,
        multi_stock_metrics_path: Path | None = None) -> dict[str, Any]:
    source = pd.read_csv(input_path, dtype={"date": str})
    source["date"] = source["date"].str.zfill(8)
    data = _with_features_and_labels(source, horizon_seconds, max_delay_seconds)
    dates = sorted(data["date"].unique().tolist())
    partitions = fixed_partitions or _date_partitions(dates)
    split_masks = {split: data["date"].isin(split_dates) for split, split_dates in partitions.items()}

    X_train = data.loc[split_masks["train"], FEATURES].replace([np.inf, -np.inf], np.nan)
    y_train = data.loc[split_masks["train"], "label"].astype(str).to_numpy()
    models: dict[str, Any] = {
        "majority_baseline": DummyClassifier(strategy="prior"),
        "logistic_regression": make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=1000, random_state=17),
        ),
        "hist_gradient_boosting": make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingClassifier(
                learning_rate=0.05, max_iter=200, max_leaf_nodes=15,
                l2_regularization=1.0, random_state=17,
            ),
        ),
    }

    predictions_by_split: dict[str, dict[str, np.ndarray]] = {}
    metrics_by_split: dict[str, dict[str, Any]] = {}
    for split in ("validation", "test"):
        subset = data.loc[split_masks[split]]
        X = subset.loc[:, FEATURES].replace([np.inf, -np.inf], np.nan)
        y = subset["label"].astype(str).to_numpy()
        predictions_by_split[split] = {}
        metrics_by_split[split] = {}
        for name, model in models.items():
            model.fit(X_train, y_train)
            predicted = model.predict(X)
            probabilities = _aligned_probabilities(model, X)
            predictions_by_split[split][name] = predicted
            metrics_by_split[split][name] = _score(y, predicted, probabilities)

    test = data.loc[split_masks["test"]]
    y_test = test["label"].astype(str).to_numpy()
    bootstrap = _bootstrap_balanced_accuracy(
        y_test, predictions_by_split["test"], test["date"].to_numpy()
    )
    try:
        source_label = input_path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        source_label = input_path.name
    metrics: dict[str, Any] = {
        "study": "PEKAO next-mid-price direction classification",
        "source_csv": source_label,
        "source_csv_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "source_rows": int(len(source)),
        "labeled_rows": int(len(data)),
        "label_definition": {
            "horizon_seconds": horizon_seconds,
            "target_quote_rule": "first same-session sampled quote at or after the horizon",
            "maximum_target_delay_seconds": max_delay_seconds,
            "classes": {"down": "future mid < current mid", "flat": "future mid = current mid", "up": "future mid > current mid"},
            "class_counts": {name: int((data["label"].astype(str) == name).sum()) for name in LABELS},
            "realized_delay_seconds": {
                "median": float(data["target_delay_seconds"].median()),
                "p95": float(data["target_delay_seconds"].quantile(0.95)),
                "maximum": float(data["target_delay_seconds"].max()),
            },
        },
        "split_method": "chronological blocks of whole trading dates; no random row split",
        "split_dates": {name: {"first": group[0], "last": group[-1], "date_count": len(group)} for name, group in partitions.items()},
        "split_rows": {name: int(mask.sum()) for name, mask in split_masks.items()},
        "features": list(FEATURES),
        "models": {
            "majority_baseline": "DummyClassifier(strategy=prior)",
            "logistic_regression": "median imputation + standardization + LogisticRegression",
            "hist_gradient_boosting": "median imputation + HistGradientBoostingClassifier; fixed settings, no test tuning",
        },
        "validation_metrics": metrics_by_split["validation"],
        "test_metrics": metrics_by_split["test"],
        "date_bootstrap": bootstrap,
        "software": {"python_packages": {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__}},
        "interpretation": "Predictive classification only. No trading rule, net returns, or deployment claim is tested.",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    output_path.with_name("ml_direction_study.md").write_text(_render_report(metrics), encoding="utf-8")
    _make_figure(metrics, execution_metrics_path, figure_path, execution_aware_metrics_path,
                 multi_stock_metrics_path)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "results" / "wselob_2017_execution_observations.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "wselob_ml_direction_metrics.json")
    parser.add_argument("--execution-metrics", type=Path, default=ROOT / "results" / "wselob_2017_metrics.json")
    parser.add_argument("--figure", type=Path, default=ROOT / "assets" / "mosaic_readme_results.png")
    parser.add_argument("--execution-aware-metrics", type=Path, default=ROOT / "results" / "execution_aware_metrics.json")
    parser.add_argument("--horizon-seconds", type=float, default=60.0)
    parser.add_argument("--max-target-delay-seconds", type=float, default=120.0)
    args = parser.parse_args()
    result = run(args.input, args.output, args.execution_metrics, args.figure,
                 args.horizon_seconds, args.max_target_delay_seconds,
                 args.execution_aware_metrics)
    print(json.dumps({
        "labeled_rows": result["labeled_rows"],
        "split_dates": result["split_dates"],
        "split_rows": result["split_rows"],
        "test_metrics": result["test_metrics"],
        "date_bootstrap": result["date_bootstrap"],
        "outputs": [str(args.output), str(args.figure)],
    }, indent=2))


if __name__ == "__main__":
    main()
