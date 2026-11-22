"""Test whether the WSELOB direction model transfers to unseen stocks."""

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
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from analyze_wselob import ROOT, enrich_day, reconstruct
from ml_direction_benchmark import (
    FEATURES,
    _aligned_probabilities,
    _bootstrap_balanced_accuracy,
    _date_partitions,
    _score,
    _with_features_and_labels,
)


TICKERS = ("KGHM", "PEKAO", "PKOBP", "PKNORLEN", "PZU")
HORIZON_SECONDS = 60.0
MAX_DELAY_SECONDS = 120.0


def _input_files(data_dir: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    data_dir = data_dir.resolve()
    for path in sorted(data_dir.rglob("*_lob_2017_zlib.h5")):
        ticker = path.name.split("_", 1)[0].upper()
        if ticker in TICKERS:
            found[ticker] = path
    missing = sorted(set(TICKERS) - set(found))
    if missing:
        raise FileNotFoundError(
            f"Missing WSELOB order-book files for {', '.join(missing)} under {data_dir}. "
            "Download the five-stock 2017 archive; see data/README.md."
        )
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build_snapshot_cache(paths: dict[str, Path], stride: int, csv_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest = {
        "stride": stride,
        "inputs": {ticker: {"path": str(path.relative_to(ROOT)), "size_bytes": path.stat().st_size,
                            "sha256": _sha256(path)} for ticker, path in paths.items()},
    }
    manifest_path = csv_path.with_suffix(".manifest.json")
    if csv_path.exists() and manifest_path.exists():
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if old_manifest.get("stride") == stride and old_manifest.get("inputs") == manifest["inputs"]:
            return pd.read_csv(csv_path, dtype={"ticker": str, "date": str}), old_manifest

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.unlink(missing_ok=True)
    source_counts: dict[str, Any] = {}
    first = True
    fields = ["ticker", "date", "event_index", "timestamp", "mid", "spread_pln",
              "ask_depth", "bid_depth", "volatility_bps_1m"]
    for ticker, path in paths.items():
        print(f"Reconstructing {ticker} from {path.name}...", flush=True)
        by_day, metadata = reconstruct(path, stride)
        stock_rows: list[dict[str, Any]] = []
        for date, raw_rows in by_day.items():
            for row in enrich_day(raw_rows):
                stock_rows.append({
                    "ticker": ticker,
                    "date": date,
                    "event_index": row["event_index"],
                    "timestamp": row["timestamp"],
                    "mid": row["mid"],
                    "spread_pln": row["spread_pln"],
                    "ask_depth": row["ask_depth"],
                    "bid_depth": row["bid_depth"],
                    "volatility_bps_1m": row["volatility_bps_1m"],
                })
        frame = pd.DataFrame(stock_rows, columns=fields)
        frame.to_csv(csv_path, index=False, mode="w" if first else "a", header=first)
        first = False
        source_counts[ticker] = {
            "source_file": path.name,
            "raw_events": metadata["raw_events"],
            "dates": metadata["date_count"],
            "usable_snapshots": len(frame),
            "reconstruction_diagnostics": metadata["reconstruction_diagnostics"],
        }
        print(f"{ticker}: {metadata['raw_events']:,} updates, {len(frame):,} usable sampled books.", flush=True)
    manifest["stock_source_counts"] = source_counts
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return pd.read_csv(csv_path, dtype={"ticker": str, "date": str}), manifest


def _fit_fold(data: pd.DataFrame, held_out: str, partitions: dict[str, list[str]]) -> dict[str, Any]:
    train_dates = set(partitions["train"])
    test_dates = set(partitions["test"])
    train = data.loc[(data["ticker"] != held_out) & data["date"].isin(train_dates)].copy()
    test = data.loc[(data["ticker"] == held_out) & data["date"].isin(test_dates)].copy()
    if train.empty or test.empty:
        raise ValueError(f"Insufficient train/test data for held-out ticker {held_out}")

    ticker_counts = train.groupby("ticker").size().to_dict()
    ticker_weights = {ticker: len(train) / (len(ticker_counts) * count)
                      for ticker, count in ticker_counts.items()}
    sample_weight = train["ticker"].map(ticker_weights).to_numpy(dtype=float)
    X_train = train.loc[:, FEATURES].replace([np.inf, -np.inf], np.nan)
    y_train = train["label"].astype(str).to_numpy()
    X_test = test.loc[:, FEATURES].replace([np.inf, -np.inf], np.nan)
    y_test = test["label"].astype(str).to_numpy()

    models = {
        "majority_baseline": DummyClassifier(strategy="prior"),
        "logistic_regression": make_pipeline(
            SimpleImputer(strategy="median"), StandardScaler(),
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
    predictions: dict[str, np.ndarray] = {}
    probabilities: dict[str, np.ndarray] = {}
    for name, model in models.items():
        if name == "majority_baseline":
            model.fit(X_train, y_train, sample_weight=sample_weight)
        elif name == "logistic_regression":
            model.fit(X_train, y_train,
                      standardscaler__sample_weight=sample_weight,
                      logisticregression__sample_weight=sample_weight)
        else:
            model.fit(X_train, y_train,
                      histgradientboostingclassifier__sample_weight=sample_weight)
        predictions[name] = model.predict(X_test)
        probabilities[name] = _aligned_probabilities(model, X_test)

    metrics = {name: _score(y_test, predictions[name], probabilities[name]) for name in models}
    bootstrap = _bootstrap_balanced_accuracy(y_test, predictions, test["date"].to_numpy())
    return {
        "held_out_ticker": held_out,
        "train_tickers": sorted(ticker_counts),
        "train_ticker_rows": {ticker: int(count) for ticker, count in ticker_counts.items()},
        "ticker_weighting": "equal total sample weight per training ticker",
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "test_dates": int(test["date"].nunique()),
        "test_date_range": [str(test["date"].min()), str(test["date"].max())],
        "test_class_counts": {label: int(np.sum(y_test == label)) for label in ("down", "flat", "up")},
        "target_delay_seconds": {"median": float(test["target_delay_seconds"].median()),
                                 "p95": float(test["target_delay_seconds"].quantile(.95))},
        "test_metrics": metrics,
        "date_bootstrap": bootstrap,
    }


def _render_report(metrics: dict[str, Any]) -> str:
    lines = [
        "# WSELOB Leave-One-Stock-Out Direction Study", "",
        "## Question", "",
        "Do order-book features learned from four WSE stocks transfer to a fifth stock that the model has never seen? This is a stricter generalization check than randomly splitting snapshots from the same instrument.", "",
        "## Protocol", "",
        f"The study reconstructs {metrics['source_totals']['usable_snapshots']:,} usable snapshots from {metrics['source_totals']['raw_events']:,} updates across five WSE instruments in 2017. For each fold, the held-out stock is excluded from all model fitting. Models train on the other four stocks’ first {metrics['date_splits']['train']['count']} dates and are evaluated on the held-out stock’s final {metrics['date_splits']['test']['count']} dates. The intervening {metrics['date_splits']['validation']['count']} dates are reserved and are not used to tune the fixed settings. Target construction uses the first same-session midpoint at or after 60 seconds, capped at 120 seconds.", "",
        "Training rows receive weights so each of the four training stocks has equal total influence. The comparison uses a training-prior baseline, regularized logistic regression, and histogram gradient boosting with the feature set and fixed settings from the PEKAO study. Confidence intervals resample complete held-out test dates.", "",
        "## Held-out results", "",
        "| Unseen stock | Test rows | Target delay median / p95 | Majority balanced accuracy | Logistic balanced accuracy | Boosted balanced accuracy | Boosted lift vs majority (95% date CI) | Boosted log loss |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for fold in metrics["folds"]:
        test = fold["test_metrics"]
        ci = fold["date_bootstrap"]["delta_vs_majority_95ci"]["hist_gradient_boosting"]
        lift = (test["hist_gradient_boosting"]["balanced_accuracy"]
                - test["majority_baseline"]["balanced_accuracy"]) * 100.0
        lines.append(
            f"| {fold['held_out_ticker']} | {fold['test_rows']:,} | {fold['target_delay_seconds']['median']:.1f}s / {fold['target_delay_seconds']['p95']:.1f}s | "
            f"{test['majority_baseline']['balanced_accuracy']:.3f} | "
            f"{test['logistic_regression']['balanced_accuracy']:.3f} | "
            f"{test['hist_gradient_boosting']['balanced_accuracy']:.3f} | "
            f"{lift:+.2f} pp [{ci[0]*100:+.2f}, {ci[1]*100:+.2f}] | "
            f"{test['hist_gradient_boosting']['log_loss']:.3f} |"
        )
    mean_lift = metrics["unweighted_mean_boosted_lift_pp"]
    lines.extend([
        "", f"Across the five held-out stocks, the unweighted mean boosted-tree lift is {mean_lift:+.2f} percentage points. Read the ticker-level results as the main evidence: this five-stock universe is small, the data are from one historical year, and the results do not establish stability in other markets or later periods.", "",
        "## Limits", "",
        "Snapshots are sampled every 100 source events, so the quote at a requested 60-second horizon arrives later and with instrument-dependent delay. This evaluates midpoint direction only; it does not establish positive returns after spread, fees, queue position, latency, or impact. The separate execution-aware PEKAO study finds negative average markout after its illustrative costs. The source data are historical and licensed CC BY 4.0; do not redistribute raw files with the software repository.", "",
        "## Reproduce", "",
        "Download and extract the five WSELOB order-book HDF5 files as described in [data/README.md](../data/README.md), then run:", "",
        "```powershell", "python multi_stock_ml_benchmark.py --data-dir data/wselob_2017 --stride 100", "```", "",
        "The script verifies and caches sampled observations locally under `build/`. Source file sizes and SHA-256 hashes, reconstruction counters, fold metrics, class counts, and date-bootstrap intervals are in the companion JSON.", "",
    ])
    return "\n".join(lines)


def run(data_dir: Path, stride: int, output_json: Path, output_report: Path,
        snapshot_cache: Path) -> dict[str, Any]:
    if stride < 1:
        raise ValueError("stride must be positive")
    paths = _input_files(data_dir)
    source, manifest = _build_snapshot_cache(paths, stride, snapshot_cache)
    dates = sorted(source["date"].unique().tolist())
    partitions = _date_partitions(dates)
    labeled = _with_features_and_labels(source, HORIZON_SECONDS, MAX_DELAY_SECONDS)
    folds = [_fit_fold(labeled, ticker, partitions) for ticker in TICKERS]
    lift_values = [
        (fold["test_metrics"]["hist_gradient_boosting"]["balanced_accuracy"]
         - fold["test_metrics"]["majority_baseline"]["balanced_accuracy"]) * 100.0
        for fold in folds
    ]
    totals = manifest["stock_source_counts"]
    result = {
        "study": "WSELOB leave-one-stock-out direction generalization",
        "source_dataset": "WSELOB-2017, DOI 10.17632/3g4mhdp899.1, CC BY 4.0",
        "source_totals": {
            "tickers": list(TICKERS),
            "raw_events": int(sum(item["raw_events"] for item in totals.values())),
            "usable_snapshots": int(sum(item["usable_snapshots"] for item in totals.values())),
            "source_counts_by_ticker": totals,
        },
        "snapshot_stride_raw_events": stride,
        "target": {"horizon_seconds": HORIZON_SECONDS, "maximum_target_delay_seconds": MAX_DELAY_SECONDS,
                   "rule": "first same-session sampled midpoint at or after the requested horizon"},
        "date_splits": {name: {"first": split[0], "last": split[-1], "count": len(split)}
                        for name, split in partitions.items()},
        "train_method": "each held-out ticker is excluded from fitting; the other four are trained on the first chronological date block with equal total sample weights by ticker",
        "model_settings": "Same nine features and fixed baseline/logistic/histogram-gradient-boosting settings as the PEKAO benchmark; no test tuning.",
        "folds": folds,
        "unweighted_mean_boosted_lift_pp": float(np.mean(lift_values)),
        "input_manifest": manifest["inputs"],
        "limitations": "Single year, five WSE instruments, sampled events, direction labels only, no costed multi-instrument trading test.",
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(_render_report(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "wselob_2017")
    parser.add_argument("--stride", type=int, default=100)
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "multi_stock_ml_metrics.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "multi_stock_ml_study.md")
    parser.add_argument("--snapshot-cache", type=Path, default=ROOT / "build" / "wselob_2017_multistock_stride100.csv")
    args = parser.parse_args()
    result = run(args.data_dir, args.stride, args.output_json, args.output_report, args.snapshot_cache)
    summary = [{"ticker": row["held_out_ticker"], "test_rows": row["test_rows"],
                "balanced_accuracy": row["test_metrics"]["hist_gradient_boosting"]["balanced_accuracy"],
                "lift_pp": (row["test_metrics"]["hist_gradient_boosting"]["balanced_accuracy"]
                            - row["test_metrics"]["majority_baseline"]["balanced_accuracy"]) * 100.0}
               for row in result["folds"]]
    print(json.dumps({"source_totals": result["source_totals"], "unweighted_mean_boosted_lift_pp": result["unweighted_mean_boosted_lift_pp"], "folds": summary, "outputs": [str(args.output_json), str(args.output_report)]}, indent=2))


if __name__ == "__main__":
    main()
