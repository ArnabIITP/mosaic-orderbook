"""Costed, chronological, leave-one-stock-out study on WSELOB-2017.

This evaluates immediate marketable round trips against reconstructed displayed
depth. WSELOB does not supply independent trade prints, so this is not a passive
queue-fill simulator or a live-trading backtest.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, recall_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from analyze_wselob import ROOT, enrich_day, reconstruct
from ml_direction_benchmark import FEATURES
from multi_stock_ml_benchmark import TICKERS, _input_files

CLASSES = ("down", "flat", "up")
SIZE = 500
HORIZON_SECONDS = 60.0
MAX_DELAY_SECONDS = 120.0
FEE_BPS_PER_SIDE = 1.0
BOOTSTRAP_REPS = 5000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sweep(levels: list[tuple[int, int]], quantity: int) -> float | None:
    remaining = quantity
    notional = 0.0
    for price, displayed in levels:
        take = min(remaining, displayed)
        notional += take * price
        remaining -= take
        if remaining == 0:
            return notional / quantity
    return None


def _features(row: dict[str, Any], index: int,
              times: np.ndarray, mids: np.ndarray) -> dict[str, float]:
    mid = float(row["mid"])
    ask = float(row["ask_depth"])
    bid = float(row["bid_depth"])
    total = ask + bid
    result = {
        "spread_bps": float(row["spread_pln"]) * 100.0 / mid * 10_000.0,
        "depth_imbalance": (bid - ask) / total if total else 0.0,
        "log_bid_depth": math.log1p(bid),
        "log_ask_depth": math.log1p(ask),
        "log_total_depth": math.log1p(total),
        "volatility_1m_bps": float(row["volatility_bps_1m"]),
    }
    for seconds in (30, 120, 300):
        prior = int(np.searchsorted(times, times[index] - seconds, side="right") - 1)
        if prior >= 0 and times[index] - times[prior] <= 2 * seconds:
            result[f"return_{seconds}s_bps"] = math.log(mids[index] / mids[prior]) * 10_000.0
        else:
            result[f"return_{seconds}s_bps"] = float("nan")
    return result


def _session_samples(ticker: str, date: str, enriched: list[dict[str, Any]],
                     fee_bps: float) -> list[dict[str, Any]]:
    if len(enriched) < 2:
        return []
    times = np.fromiter((float(row["timestamp"]) for row in enriched), dtype=np.float64)
    mids = np.fromiter((float(row["mid"]) for row in enriched), dtype=np.float64)
    samples: list[dict[str, Any]] = []
    for i, row in enumerate(enriched):
        target_i = int(np.searchsorted(times, times[i] + HORIZON_SECONDS, side="left"))
        if target_i >= len(enriched):
            continue
        delay = times[target_i] - times[i]
        if delay > MAX_DELAY_SECONDS:
            continue
        target = enriched[target_i]
        buy_entry = _sweep(row["asks"], SIZE)
        sell_entry = _sweep(row["bids"], SIZE)
        sell_exit = _sweep(target["bids"], SIZE)
        buy_exit = _sweep(target["asks"], SIZE)
        if None in (buy_entry, sell_entry, sell_exit, buy_exit):
            continue
        long_bps = (float(sell_exit) - float(buy_entry)) / mids[i] * 10_000.0 - 2 * fee_bps
        short_bps = (float(sell_entry) - float(buy_exit)) / mids[i] * 10_000.0 - 2 * fee_bps
        if max(long_bps, short_bps) <= 0:
            label = "flat"
        elif long_bps >= short_bps:
            label = "up"
        else:
            label = "down"
        features = _features(row, i, times, mids)
        samples.append({"ticker": ticker, "date": date,
            "event_index": int(row["event_index"]), "timestamp": float(row["timestamp"]),
            "future_timestamp": float(target["timestamp"]), "target_delay_seconds": float(delay),
            "mid": mids[i], "label": label, "net_up_bps": long_bps, "net_down_bps": short_bps,
            **features})
    return samples


def _date_split(dates: list[str]) -> dict[str, list[str]]:
    if len(dates) < 30:
        raise ValueError(f"Expected at least 30 sessions, found {len(dates)}")
    train_end = math.floor(.70 * len(dates))
    validation_end = math.floor(.85 * len(dates))
    return {"train": dates[:train_end], "validation": dates[train_end:validation_end],
            "test": dates[validation_end:]}


def _trade_metrics(frame: pd.DataFrame, predictions: np.ndarray) -> dict[str, Any]:
    ordered = frame.assign(prediction=predictions).sort_values(["date", "ticker", "timestamp"], kind="stable")
    chosen: list[tuple[str, str, float]] = []
    last_exit: dict[tuple[str, str], float] = {}
    for row in ordered.itertuples():
        key = (row.ticker, row.date)
        if row.timestamp < last_exit.get(key, -float("inf")) or row.prediction == "flat":
            continue
        net = float(row.net_up_bps if row.prediction == "up" else row.net_down_bps)
        chosen.append((row.date, row.ticker, net))
        last_exit[key] = float(row.future_timestamp)
    daily: dict[str, list[float]] = {}
    for date, _ticker, value in chosen:
        daily.setdefault(date, []).append(value)
    day_means = np.asarray([np.mean(values) for values in daily.values()], dtype=float)
    if len(day_means):
        rng = np.random.default_rng(20261122)
        boot = np.mean(day_means[rng.integers(0, len(day_means), size=(BOOTSTRAP_REPS, len(day_means)))], axis=1)
        ci = [float(x) for x in np.quantile(boot, [.025, .975])]
    else:
        ci = [None, None]
    return {"non_overlapping_round_trips": len(chosen), "active_test_dates": len(daily),
        "mean_net_bps_per_trade": float(np.mean([x[2] for x in chosen])) if chosen else None,
        "median_net_bps_per_trade": float(np.median([x[2] for x in chosen])) if chosen else None,
        "profitable_trade_rate_pct": float(np.mean([x[2] > 0 for x in chosen]) * 100) if chosen else None,
        "equal_weight_mean_daily_bps": float(day_means.mean()) if len(day_means) else None,
        "equal_weight_date_bootstrap_95ci_bps": ci,
        "per_ticker_non_overlapping_trips": {ticker: sum(1 for _, t, _ in chosen if t == ticker) for ticker in TICKERS}}


def _fit_model(name: str) -> Any:
    if name == "majority_baseline":
        return DummyClassifier(strategy="prior")
    if name == "logistic_regression":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             LogisticRegression(max_iter=1000, random_state=37))
    return make_pipeline(SimpleImputer(strategy="median"), HistGradientBoostingClassifier(
        learning_rate=.05, max_iter=200, max_leaf_nodes=15, l2_regularization=1.0, random_state=37))


def run(data_dir: Path, stride: int, order_size: int, fee_bps: float,
        output_json: Path, output_report: Path) -> dict[str, Any]:
    if stride < 1 or order_size < 1 or fee_bps < 0:
        raise ValueError("stride and order size must be positive; fee cannot be negative")
    global SIZE
    SIZE = order_size
    paths = _input_files(data_dir)
    samples: list[dict[str, Any]] = []
    source_manifest: dict[str, Any] = {}
    for ticker, path in paths.items():
        print(f"Reconstructing {ticker} event books...", flush=True)
        by_day, metadata = reconstruct(path, stride)
        ticker_samples = 0
        for date, raw in by_day.items():
            for row in _session_samples(ticker, date, enrich_day(raw), fee_bps):
                samples.append(row)
                ticker_samples += 1
        source_manifest[ticker] = {"filename": path.name, "size_bytes": path.stat().st_size,
            "sha256": _sha256(path), "sessions": metadata["date_count"],
            "raw_events": metadata["raw_events"], "usable_samples": ticker_samples,
            "reconstruction_diagnostics": metadata["reconstruction_diagnostics"]}
        del by_day
        gc.collect()
        print(f"{ticker}: {ticker_samples:,} fully displayed-depth-fillable samples", flush=True)
    frame = pd.DataFrame(samples)
    del samples
    frame["date"] = frame["date"].astype(str)
    dates = sorted(frame["date"].unique().tolist())
    partitions = _date_split(dates)
    train_dates, test_dates = set(partitions["train"]), set(partitions["test"])
    model_names = ("majority_baseline", "logistic_regression", "hist_gradient_boosting")
    folds = []
    pooled_parts: dict[str, list[pd.DataFrame]] = {name: [] for name in model_names}
    pooled_predictions: dict[str, list[np.ndarray]] = {name: [] for name in model_names}
    for heldout in TICKERS:
        train = frame.loc[(frame.ticker != heldout) & frame.date.isin(train_dates)].copy()
        test = frame.loc[(frame.ticker == heldout) & frame.date.isin(test_dates)].copy()
        x_train = train.loc[:, FEATURES].replace([np.inf, -np.inf], np.nan)
        y_train = train.label.to_numpy()
        x_test = test.loc[:, FEATURES].replace([np.inf, -np.inf], np.nan)
        y_test = test.label.to_numpy()
        models: dict[str, Any] = {}
        scores: dict[str, Any] = {}
        for name in model_names:
            model = _fit_model(name)
            model.fit(x_train, y_train)
            pred = model.predict(x_test)
            pooled_parts[name].append(test)
            pooled_predictions[name].append(pred)
            models[name] = model
            scores[name] = {"balanced_accuracy": float(recall_score(y_test, pred, labels=list(CLASSES), average="macro", zero_division=0)),
                "accuracy": float(accuracy_score(y_test, pred)),
                "macro_f1": float(f1_score(y_test, pred, labels=list(CLASSES), average="macro", zero_division=0)),
                "target_counts": {c: int(np.sum(y_test == c)) for c in CLASSES},
                "predicted_counts": {c: int(np.sum(pred == c)) for c in CLASSES},
                "trading_metrics": _trade_metrics(test, pred)}
        folds.append({"heldout_ticker": heldout, "train_tickers": sorted(train.ticker.unique().tolist()),
            "train_rows": len(train), "test_rows": len(test), "test_dates": int(test.date.nunique()),
            "test_class_counts": {c: int(np.sum(y_test == c)) for c in CLASSES},
            "scores": scores})
    metrics = {"study": "multi-session, five-stock costed executable-target cross-stock study",
        "dataset": "WSELOB-2017, DOI 10.17632/3g4mhdp899.1, CC BY 4.0",
        "source_manifest": source_manifest,
        "observations": {"raw_events": int(sum(x["raw_events"] for x in source_manifest.values())),
            "usable_samples": int(len(frame)), "sessions": len(dates), "tickers": list(TICKERS)},
        "protocol": {"chronological_split": {k: {"first": v[0], "last": v[-1], "sessions": len(v)} for k, v in partitions.items()},
            "train": "first 70% of sessions; held-out instrument excluded; all forward labels remain within their session",
            "validation": "middle 15% reserved and unused; model settings fixed in advance",
            "test": "final 15% sessions; one held-out ticker at a time",
            "target": f"direction with positive net hypothetical {order_size}-share marketable round-trip outcome at first sampled book >= {HORIZON_SECONDS:g}s, max delay {MAX_DELAY_SECONDS:g}s; otherwise flat",
            "execution": "sweep up to ten reconstructed displayed levels at entry and target; require full size at all four side/time sweeps; charge stated fee on each side",
            "fee_bps_per_side": fee_bps, "order_size_shares": order_size,
            "models": "training-prior baseline, regularized logistic regression, fixed histogram gradient boosting; no test tuning",
            "trade_policy": "flat predictions skip; directional calls executed sequentially within each ticker-session, next entry only after prior target timestamp",
            "uncertainty": f"seeded bootstrap over active test-session mean returns ({BOOTSTRAP_REPS} replicates), equal weight per active session"},
        "folds": folds,
        "pooled_non_overlapping_trades": {},
        "limitations": ["WSELOB does not provide an independent trade-print stream; passive fills and queue priority are not validated.",
            "Displayed-book sweeps are hypothetical immediate marketable fills; no arrival latency, intervening event path, hidden liquidity, market impact, or market interaction.",
            "Target rows with incomplete displayed depth are excluded; resulting sample is conditional on full visible fillability.",
            "The 2017 WSE universe has five instruments and one year; this is not evidence of performance on current markets or other venues.",
            "Trade P&L diagnostics use a one-position-per-instrument policy but omit capital constraints, portfolio netting, and risk controls."]}
    # Aggregate held-out outcomes by model without refitting or reordering folds.
    pooled: dict[str, Any] = {}
    for name in model_names:
        pooled_frame = pd.concat(pooled_parts[name], ignore_index=True)
        predictions = np.concatenate(pooled_predictions[name])
        pooled[name] = _trade_metrics(pooled_frame, predictions)
    metrics["pooled_non_overlapping_trades"] = pooled
    metrics["interpretation"] = "Exploratory cross-stock model selection diagnostic on simulated displayed-depth round trips; not a passive queue study or deployable strategy claim."
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    report = _render_report(metrics)
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text(report, encoding="utf-8")
    return metrics


def _render_report(metrics: dict[str, Any]) -> str:
    p = metrics["protocol"]
    lines = ["# WSELOB Multi-Session Executable-Outcome Study", "",
        "## Research question", "",
        "Can a model trained on four Warsaw Stock Exchange instruments identify, on an unseen fifth instrument, when backward-looking book features correspond to a positive hypothetical marketable round trip after displayed spread/depth costs and fees?", "",
        "## Data and protocol", "",
        f"The study reconstructs {metrics['observations']['raw_events']:,} updates and evaluates {metrics['observations']['usable_samples']:,} full-depth-eligible samples over {metrics['observations']['sessions']} sessions and five instruments from WSELOB-2017. Each prediction is evaluated at the first same-session sampled book at least {HORIZON_SECONDS:g} seconds later, capped at {MAX_DELAY_SECONDS:g} seconds. The direction label is the better of long or short only if its hypothetical net round trip is positive after {2 * p['fee_bps_per_side']:.2f} bps round-trip fees; otherwise it is flat.", "",
        f"For each {p['order_size_shares']}-share intent, both entry and exit prices are swept through up to ten displayed levels. Rows require full visible size on both sides at both times. The first 70% of sessions train, the middle 15% are reserved and unused, and the final 15% are held out. For each held-out instrument, that instrument is excluded from all fitting. Settings are fixed; no test-period tuning is performed.", "",
        "## Held-out results", "",
        "| Unseen ticker | Test sessions | Eligible rows | Target flat / down / up | Model | Balanced accuracy | Predicted flat / down / up | Sequential round trips | Mean net bps/trip | Date-bootstrap 95% interval |", "|---|---:|---:|---:|---|---:|---|---:|---:|---:|"]
    for fold in metrics["folds"]:
        for name, result in fold["scores"].items():
            counts = result["target_counts"]
            pred = result["predicted_counts"]
            trade = result["trading_metrics"]
            mean = trade["mean_net_bps_per_trade"]
            ci = trade["equal_weight_date_bootstrap_95ci_bps"]
            mean_text = f"{mean:.2f}" if mean is not None else "n/a"
            ci_text = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci[0] is not None else "n/a"
            lines.append(f"| {fold['heldout_ticker']} | {fold['test_dates']} | {fold['test_rows']:,} | {counts['flat']:,} / {counts['down']:,} / {counts['up']:,} | {name} | {result['balanced_accuracy']:.3f} | {pred['flat']:,} / {pred['down']:,} / {pred['up']:,} | {trade['non_overlapping_round_trips']:,} | {mean_text} | {ci_text} |")
    lines.extend(["", "### Pooled sequential held-out policy outcomes", "",
        "| Model | Non-overlapping round trips | Active sessions | Mean net bps/trip | Equal-weight daily mean (bps) | Session-bootstrap 95% interval | Profitable trips |", "|---|---:|---:|---:|---:|---:|---:|"])
    for name, row in metrics["pooled_non_overlapping_trades"].items():
        ci = row["equal_weight_date_bootstrap_95ci_bps"]
        mean_text = f"{row['mean_net_bps_per_trade']:.2f}" if row["mean_net_bps_per_trade"] is not None else "n/a"
        daily_text = f"{row['equal_weight_mean_daily_bps']:.2f}" if row["equal_weight_mean_daily_bps"] is not None else "n/a"
        ci_text = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci[0] is not None else "n/a"
        win_text = f"{row['profitable_trade_rate_pct']:.1f}%" if row["profitable_trade_rate_pct"] is not None else "n/a"
        lines.append(f"| {name} | {row['non_overlapping_round_trips']:,} | {row['active_test_dates']} | {mean_text} | {daily_text} | {ci_text} | {win_text} |")
    lines.extend(["", "## Interpretation", "",
        "This is a stronger test of cross-instrument, after-cost prediction than midpoint direction alone. It remains an offline, sampled-book simulation. A positive score would not establish live profitability; negative or weak performance is still informative evidence about the proposed signal and model. Read the per-ticker rows alongside the pooled diagnostic rather than treating the pooled number as a universal result.", "",
        "## Limitations", "", "- WSELOB contains order-book updates but no independent trade-print stream in this project; it cannot validate passive executions, queue-ahead depletion, or fill adverse selection.",
        "- Entry and exit assume immediate access to displayed depth at sampled timestamps. Arrival delay, intervening events, hidden liquidity, replenishment, market impact, and interaction with the order are not modeled.",
        "- Full-size displayed-depth eligibility selects the sample and excludes orders that could not be fully swept in the visible book.",
        "- The data cover five WSE names in 2017 only; results do not establish behavior in other venues, instruments, or current markets.",
        "- The sequential policy allows one open simulated position per ticker but does not include capital allocation, portfolio risk limits, or live order handling.", "",
        "## Reproduction", "", "From the repository root, with the five WSELOB files installed as described in [data/README.md](../data/README.md):", "",
        "```powershell", "python multistock_executable_study.py --data-dir data/wselob_2017 --stride 100 --order-size 500 --fee-bps-per-side 1", "```", "",
        "The output JSON records per-input SHA-256 hashes, row counts, splits, class balance, model metrics, simulated trade outcomes, and the limitations.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "wselob_2017")
    parser.add_argument("--stride", type=int, default=100)
    parser.add_argument("--order-size", type=int, default=SIZE)
    parser.add_argument("--fee-bps-per-side", type=float, default=FEE_BPS_PER_SIDE)
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "multistock_executable_metrics.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "multistock_executable_study.md")
    args = parser.parse_args()
    result = run(args.data_dir, args.stride, args.order_size, args.fee_bps_per_side, args.output_json, args.output_report)
    print(json.dumps({"observations": result["observations"], "splits": result["protocol"]["chronological_split"],
        "pooled_non_overlapping_trades": result["pooled_non_overlapping_trades"],
        "outputs": [str(args.output_json), str(args.output_report)]}, indent=2))


if __name__ == "__main__":
    main()
