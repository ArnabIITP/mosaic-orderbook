"""Run an expanding-window, embargoed PEKAO direction benchmark with feature ablations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ml_direction_benchmark import (
    FEATURES, LABELS, _aligned_probabilities, _bootstrap_balanced_accuracy,
    _score, _with_features_and_labels,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "results" / "wselob_2017_execution_observations.csv"
INITIAL_TRAIN_DATES = 100
TEST_DATES = 25
EMBARGO_DATES = 5
FOLDS = 5
BOOTSTRAP_REPS = 2000


def _hgb() -> Any:
    return make_pipeline(
        SimpleImputer(strategy="median"),
        HistGradientBoostingClassifier(learning_rate=0.05, max_iter=200,
                                       max_leaf_nodes=15, l2_regularization=1.0,
                                       random_state=17),
    )


def _model_specs() -> dict[str, tuple[Any, tuple[str, ...]]]:
    book_features = {"spread_bps", "depth_imbalance", "log_bid_depth", "log_ask_depth", "log_total_depth"}
    return {
        "majority_baseline": (DummyClassifier(strategy="prior"), FEATURES),
        "logistic_regression": (make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
            LogisticRegression(max_iter=1000, random_state=17),
        ), FEATURES),
        "hist_gradient_boosting": (_hgb(), FEATURES),
        "hgb_without_recent_returns": (_hgb(), tuple(f for f in FEATURES if not f.startswith("return_"))),
        "hgb_without_book_features": (_hgb(), tuple(f for f in FEATURES if f not in book_features)),
        "hgb_without_volatility": (_hgb(), tuple(f for f in FEATURES if f != "volatility_1m_bps")),
    }


def run(input_path: Path, output_json: Path, output_report: Path) -> dict[str, Any]:
    source = pd.read_csv(input_path, dtype={"date": str})
    source["date"] = source["date"].str.zfill(8)
    data = _with_features_and_labels(source, 60.0, 120.0)
    dates = sorted(data["date"].unique())
    specs = _model_specs()
    fold_specs = []
    for fold in range(FOLDS):
        test_start = INITIAL_TRAIN_DATES + EMBARGO_DATES + fold * (TEST_DATES + EMBARGO_DATES)
        test_end = min(test_start + TEST_DATES, len(dates))
        train_end = test_start - EMBARGO_DATES
        if test_end <= test_start or train_end < INITIAL_TRAIN_DATES:
            break
        fold_specs.append({"train": dates[:train_end], "embargo": dates[train_end:test_start], "test": dates[test_start:test_end]})

    folds: list[dict[str, Any]] = []
    all_y: list[np.ndarray] = []
    all_dates: list[np.ndarray] = []
    pooled_predictions: dict[str, list[np.ndarray]] = {name: [] for name in specs}
    for fold_index, spec in enumerate(fold_specs, start=1):
        train = data.loc[data["date"].isin(spec["train"])]
        test = data.loc[data["date"].isin(spec["test"])]
        y_train = train["label"].astype(str).to_numpy()
        y_test = test["label"].astype(str).to_numpy()
        predictions: dict[str, np.ndarray] = {}
        probabilities: dict[str, np.ndarray] = {}
        for name, (model, features) in specs.items():
            X_train = train.loc[:, features].replace([np.inf, -np.inf], np.nan)
            X_test = test.loc[:, features].replace([np.inf, -np.inf], np.nan)
            model.fit(X_train, y_train)
            predictions[name] = model.predict(X_test)
            probabilities[name] = _aligned_probabilities(model, X_test)
            pooled_predictions[name].append(predictions[name])
        all_y.append(y_test)
        all_dates.append(test["date"].to_numpy())
        folds.append({
            "fold": fold_index,
            "train_dates": {"first": spec["train"][0], "last": spec["train"][-1], "count": len(spec["train"])},
            "embargo_dates": {"first": spec["embargo"][0], "last": spec["embargo"][-1], "count": len(spec["embargo"])},
            "test_dates": {"first": spec["test"][0], "last": spec["test"][-1], "count": len(spec["test"])},
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "scores": {name: _score(y_test, predictions[name], probabilities[name]) for name in predictions},
        })

    y_all = np.concatenate(all_y)
    date_all = np.concatenate(all_dates)
    preds_all = {name: np.concatenate(parts) for name, parts in pooled_predictions.items()}
    pooled_ci = _bootstrap_balanced_accuracy(y_all, preds_all, date_all, reps=BOOTSTRAP_REPS)
    pooled_ba = {name: float(balanced_accuracy_score(y_all, pred)) for name, pred in preds_all.items()}
    metrics = {
        "study": "PEKAO expanding-window walk-forward direction benchmark",
        "source_csv": input_path.name,
        "source_csv_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "source_rows": int(len(source)),
        "labeled_rows": int(len(data)),
        "target": {"direction": "first same-session midpoint at or after 60 seconds, max delay 120 seconds"},
        "protocol": {
            "initial_training_sessions": INITIAL_TRAIN_DATES,
            "embargo_sessions": EMBARGO_DATES,
            "test_sessions_per_fold": TEST_DATES,
            "expanding_window_folds": len(folds),
            "hyperparameters": "fixed to the existing PEKAO benchmark; no tuning in these folds",
            "bootstrap_replicates": BOOTSTRAP_REPS,
            "ablations": {
                "hgb_without_recent_returns": "remove trailing 30s, 120s, and 300s return features",
                "hgb_without_book_features": "remove spread, depth imbalance, and bid/ask/total depth features",
                "hgb_without_volatility": "remove trailing one-minute realized volatility",
            },
        },
        "folds": folds,
        "pooled_out_of_fold_balanced_accuracy": pooled_ba,
        "pooled_date_bootstrap_95ci": pooled_ci,
        "note": "Whole sessions are held out, with five full sessions between train and test. This avoids within-session overlapping labels across split boundaries; performance remains specific to one instrument and one historical year.",
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# PEKAO Walk-Forward Direction Study", "",
        "## Protocol", "",
        f"The fixed direction benchmark is evaluated on {len(folds)} chronological test blocks. Each fold trains on an expanding history, leaves {EMBARGO_DATES} full sessions between train and test, and scores the next {TEST_DATES} sessions. Labels use same-session future midpoints at or after 60 seconds, capped at 120 seconds. Whole-session splits keep overlapping within-session labels from crossing split boundaries.", "",
        "Each model is refit from scratch in each fold. Hyperparameters stay fixed; no tuning occurs on the reported test blocks. Five complete sessions are embargoed between train and test. Confidence intervals resample held-out dates and compare each model to the training-prior baseline.", "",
        "## Fold results", "",
        "| Fold | Train sessions | Embargo | Test sessions | Test rows | Prior BA | Logistic BA | Boosted BA | Boosted log loss |", "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for fold in folds:
        s = fold["scores"]
        lines.append(f"| {fold['fold']} | {fold['train_dates']['count']} ({fold['train_dates']['first']}–{fold['train_dates']['last']}) | {fold['embargo_dates']['count']} | {fold['test_dates']['count']} ({fold['test_dates']['first']}–{fold['test_dates']['last']}) | {fold['test_rows']:,} | {s['majority_baseline']['balanced_accuracy']:.3f} | {s['logistic_regression']['balanced_accuracy']:.3f} | {s['hist_gradient_boosting']['balanced_accuracy']:.3f} | {s['hist_gradient_boosting']['log_loss']:.3f} |")
    full_ci = pooled_ci["delta_vs_majority_95ci"]["hist_gradient_boosting"]
    lines.extend([
        "", f"Pooled out-of-fold balanced accuracy: prior {pooled_ba['majority_baseline']:.3f}, logistic {pooled_ba['logistic_regression']:.3f}, boosted {pooled_ba['hist_gradient_boosting']:.3f}. The boosted-minus-prior date-bootstrap 95% interval is [{full_ci[0]:.3f}, {full_ci[1]:.3f}].", "",
        "## Feature ablations", "",
        "The same fixed tree is refit after removing one feature family. These are descriptive ablations, not retuned models.", "",
        "| Feature set | Pooled out-of-fold BA | Lift vs prior | Date-bootstrap 95% lift interval |", "|---|---:|---:|---:|",
    ])
    labels = (("hist_gradient_boosting", "All features"),
              ("hgb_without_recent_returns", "Without recent returns"),
              ("hgb_without_book_features", "Without book features"),
              ("hgb_without_volatility", "Without volatility"))
    for name, label in labels:
        interval = pooled_ci["delta_vs_majority_95ci"][name]
        lines.append(f"| {label} | {pooled_ba[name]:.3f} | {pooled_ba[name] - pooled_ba['majority_baseline']:+.3f} | [{interval[0]:+.3f}, {interval[1]:+.3f}] |")
    lines.extend([
        "", "## Limits", "",
        "This measures midpoint classification, not executable P&L. The folds cover one WSE instrument and one historical year; an embargo reduces near-boundary overlap but does not eliminate market-regime dependence. Feature ablations are correlated comparisons and are not causal attribution. See the queue-aware sensitivity and cost-aware markout reports before drawing trading conclusions.", "",
        "Reproduce after running `python analyze_wselob.py --stride 100 --fee-bps 1`:", "",
        "```powershell", "python walk_forward_benchmark.py", "```", "",
    ])
    output_report.write_text("\n".join(lines), encoding="utf-8")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "walk_forward_metrics.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "walk_forward_study.md")
    args = parser.parse_args()
    result = run(args.input, args.output_json, args.output_report)
    print(json.dumps({
        "folds": len(result["folds"]),
        "pooled_balanced_accuracy": result["pooled_out_of_fold_balanced_accuracy"],
        "boosted_date_bootstrap_ci": result["pooled_date_bootstrap_95ci"]["delta_vs_majority_95ci"]["hist_gradient_boosting"],
        "outputs": [str(args.output_json), str(args.output_report)],
    }, indent=2))


if __name__ == "__main__":
    main()
