"""Cross-stock, time-ordered ML benchmark for executable LOBSTER round trips."""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, accuracy_score, recall_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from queue_execution import market_sweep

ROOT = Path(__file__).resolve().parent
DEFAULT_DIRS = (ROOT / "data" / "lobster_amzn_2012-06-21",
                ROOT / "data" / "lobster_other_tickers_2012-06-21")
FEATURES = ("depth_imbalance", "spread_bps", "log_top5_depth", "ofi_100_norm",
            "return_1s_bps", "volatility_1s_bps")
CLASSES = ("down", "flat", "up")
SIZE = 100
FEE_BPS = 1.0
EVENT_STRIDE = 100
TARGET_MIN_SECONDS = 1.0
TARGET_MAX_SECONDS = 2.0


def _read_ticker(message_path: Path, stride: int) -> list[dict[str, Any]]:
    book_path = message_path.with_name(message_path.name.replace("_message_", "_orderbook_"))
    sampled: list[dict[str, Any]] = []
    flow_window: deque[tuple[float, float]] = deque(maxlen=100)
    with message_path.open(newline="", encoding="utf-8") as mf, book_path.open(newline="", encoding="utf-8") as bf:
        for index, (msg, book_row) in enumerate(zip(csv.reader(mf), csv.reader(bf), strict=True)):
            timestamp = float(msg[0])
            kind, size, direction = int(msg[1]), int(msg[3]), int(msg[5])
            flow = direction * size * (1 if kind == 1 else -1) if kind in (1, 2, 3, 4) else 0.0
            flow_window.append((flow, abs(flow)))
            if index % stride:
                continue
            raw = [int(x) for x in book_row]
            asks = [(raw[i], raw[i + 1]) for i in range(0, 20, 4)
                    if 0 < raw[i] < 9_000_000_000 and raw[i + 1] > 0]
            bids = [(raw[i + 2], raw[i + 3]) for i in range(0, 20, 4)
                    if 0 < raw[i + 2] < 9_000_000_000 and raw[i + 3] > 0]
            asks.sort()
            bids.sort(reverse=True)
            if not asks or not bids or asks[0][0] <= bids[0][0]:
                continue
            bid_depth = sum(q for _, q in bids)
            ask_depth = sum(q for _, q in asks)
            depth = bid_depth + ask_depth
            ofi_den = sum(v for _, v in flow_window)
            sampled.append({"ticker": message_path.name.split("_")[0], "event_index": index,
                "timestamp": timestamp, "mid_ticks": (asks[0][0] + bids[0][0]) / 2,
                "asks": asks, "bids": bids,
                "depth_imbalance": (bid_depth - ask_depth) / depth if depth else 0.0,
                "spread_bps": (asks[0][0] - bids[0][0]) / ((asks[0][0] + bids[0][0]) / 2) * 10_000,
                "log_top5_depth": math.log1p(depth),
                "ofi_100_norm": sum(v for v, _ in flow_window) / ofi_den if ofi_den else 0.0})
    if not sampled:
        raise ValueError(f"No valid books in {message_path}")
    times = [row["timestamp"] for row in sampled]
    mids = [row["mid_ticks"] for row in sampled]
    eligible = []
    for i, row in enumerate(sampled):
        past = bisect.bisect_right(times, row["timestamp"] - 1.0, 0, i) - 1
        if past >= 0:
            row["return_1s_bps"] = (mids[i] / mids[past] - 1.0) * 10_000
            left = bisect.bisect_left(times, row["timestamp"] - 1.0, 0, i + 1)
            returns = [math.log(mids[j] / mids[j - 1]) * 10_000 for j in range(max(1, left), i + 1)]
            row["volatility_1s_bps"] = float(np.sqrt(np.dot(returns, returns)))
        else:
            row["return_1s_bps"] = float("nan")
            row["volatility_1s_bps"] = float("nan")
        future = bisect.bisect_left(times, row["timestamp"] + TARGET_MIN_SECONDS, i + 1)
        if future >= len(sampled) or times[future] - row["timestamp"] > TARGET_MAX_SECONDS:
            continue
        target = sampled[future]
        buy_entry = market_sweep(row["asks"], SIZE)
        sell_entry = market_sweep(row["bids"], SIZE)
        sell_exit = market_sweep(target["bids"], SIZE)
        buy_exit = market_sweep(target["asks"], SIZE)
        if any(out["filled_quantity"] != SIZE for out in (buy_entry, sell_entry, sell_exit, buy_exit)):
            continue
        mid = row["mid_ticks"]
        row["future_timestamp"] = target["timestamp"]
        row["target_delay_seconds"] = target["timestamp"] - row["timestamp"]
        row["net_buy_bps"] = ((float(sell_exit["vwap"]) - float(buy_entry["vwap"])) / mid * 10_000
                               - 2 * FEE_BPS)
        row["net_sell_bps"] = ((float(sell_entry["vwap"]) - float(buy_exit["vwap"])) / mid * 10_000
                                - 2 * FEE_BPS)
        best = max(row["net_buy_bps"], row["net_sell_bps"])
        row["label"] = "up" if best > 0 and row["net_buy_bps"] >= row["net_sell_bps"] else (
            "down" if best > 0 else "flat")
        row["buy_entry_vwap_ticks"] = float(buy_entry["vwap"])
        row["sell_entry_vwap_ticks"] = float(sell_entry["vwap"])
        row["sell_exit_vwap_ticks"] = float(sell_exit["vwap"])
        row["buy_exit_vwap_ticks"] = float(buy_exit["vwap"])
        eligible.append(row)
    return eligible


def _models() -> dict[str, tuple[Any, tuple[str, ...]]]:
    hgb = lambda: make_pipeline(SimpleImputer(strategy="median"),
        HistGradientBoostingClassifier(learning_rate=.05, max_iter=150, max_leaf_nodes=15,
                                       l2_regularization=1.0, random_state=37))
    return {
        "prior_baseline": (DummyClassifier(strategy="prior"), FEATURES),
        "logistic_regression": (make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
            LogisticRegression(max_iter=1000, random_state=37)), FEATURES),
        "hist_gradient_boosting": (hgb(), FEATURES),
        "hgb_without_order_flow": (hgb(), tuple(f for f in FEATURES if f != "ofi_100_norm")),
        "hgb_book_only": (hgb(), ("depth_imbalance", "spread_bps", "log_top5_depth")),
    }


def _trade_metrics(frame: pd.DataFrame, predictions: np.ndarray) -> dict[str, Any]:
    rows = frame.assign(prediction=predictions).sort_values("timestamp")
    last_exit = -float("inf")
    net_returns, dollars, notionals = [], [], []
    for row in rows.itertuples():
        if row.timestamp < last_exit or row.prediction == "flat":
            continue
        side = row.prediction
        net = row.net_buy_bps if side == "up" else row.net_sell_bps
        notional = SIZE * row.mid_ticks / 10_000
        net_returns.append(float(net))
        dollars.append(float(net) * notional / 10_000)
        notionals.append(notional)
        last_exit = row.future_timestamp
    return {"non_overlapping_trades": len(net_returns),
        "mean_net_bps_per_trade": float(np.mean(net_returns)) if net_returns else None,
        "win_rate_pct": float(np.mean(np.asarray(net_returns) > 0) * 100) if net_returns else None,
        "net_pnl_usd": float(np.sum(dollars)), "traded_notional_usd": float(2 * np.sum(notionals)),
        "net_bps_per_round_trip_turnover": (float(np.sum(dollars) / (2 * np.sum(notionals)) * 10_000)
                                              if notionals else None)}


def _fmt_number(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


def run(data_dirs: tuple[Path, ...], output_json: Path, output_report: Path,
        event_stride: int = EVENT_STRIDE) -> dict[str, Any]:
    if event_stride < 1:
        raise ValueError("event stride must be positive")
    files = sorted(file for directory in data_dirs if directory.exists()
                   for file in directory.glob("*_message_5.csv"))
    if len(files) < 2:
        raise FileNotFoundError("At least two LOBSTER ticker message files are required")
    stocks = {path.name.split("_")[0]: _read_ticker(path, event_stride) for path in files}
    # Per-ticker chronological cutoffs prevent future labels leaking into train.
    train_rows, test_by_ticker = [], {}
    for ticker, rows in stocks.items():
        start, end = rows[0]["timestamp"], rows[-1]["timestamp"]
        train_cut = start + .70 * (end - start)
        test_cut = start + .80 * (end - start)
        train_rows.extend(row for row in rows if row["timestamp"] < train_cut
                          and row["future_timestamp"] <= train_cut)
        test_by_ticker[ticker] = [row for row in rows if row["timestamp"] >= test_cut]
    specs = _models()
    fold_reports, all_scores = [], {name: {"truth": [], "prediction": []} for name in specs}
    all_trade_metrics: dict[str, list[dict[str, Any]]] = {name: [] for name in specs}
    for heldout in sorted(stocks):
        train = [row for row in train_rows if row["ticker"] != heldout]
        test = test_by_ticker[heldout]
        if not train or not test:
            continue
        X_train = pd.DataFrame(train)[list(FEATURES)].replace([np.inf, -np.inf], np.nan)
        y_train = np.asarray([row["label"] for row in train])
        X_test = pd.DataFrame(test)[list(FEATURES)].replace([np.inf, -np.inf], np.nan)
        y_test = np.asarray([row["label"] for row in test])
        fold_predictions = {}
        fold_scores = {}
        fold_trades = {}
        test_frame = pd.DataFrame(test)
        for name, (model, features) in specs.items():
            if len(np.unique(y_train)) == 1:
                prediction = np.full(len(y_test), y_train[0], dtype=object)
            else:
                model.fit(X_train.loc[:, features], y_train)
                prediction = model.predict(X_test.loc[:, features])
            fold_predictions[name] = prediction
            all_scores[name]["truth"].extend(y_test.tolist())
            all_scores[name]["prediction"].extend(prediction.tolist())
            fold_scores[name] = {"balanced_accuracy": float(recall_score(y_test, prediction, labels=list(CLASSES), average="macro", zero_division=0)),
                "accuracy": float(accuracy_score(y_test, prediction)),
                "macro_f1": float(f1_score(y_test, prediction, labels=list(CLASSES), average="macro", zero_division=0)),
                "predicted_counts": {label: int(np.sum(prediction == label)) for label in CLASSES},
                "test_target_counts": {label: int(np.sum(y_test == label)) for label in CLASSES}}
            trade = _trade_metrics(test_frame, prediction)
            all_trade_metrics[name].append(trade)
            fold_trades[name] = trade
        fold_reports.append({"heldout_ticker": heldout, "train_tickers": sorted(set(r["ticker"] for r in train)),
            "train_rows": len(train), "train_target_counts": {label: int(np.sum(y_train == label)) for label in CLASSES},
            "test_rows": len(test), "test_time_start": min(r["timestamp"] for r in test),
            "test_time_end": max(r["timestamp"] for r in test), "scores": fold_scores,
            "non_overlapping_trade_metrics": fold_trades})
    pooled_scores = {}
    for name, values in all_scores.items():
        y_true, y_pred = values["truth"], values["prediction"]
        pooled_scores[name] = {"balanced_accuracy": float(recall_score(y_true, y_pred, labels=list(CLASSES), average="macro", zero_division=0)),
            "accuracy": float(accuracy_score(y_true, y_pred)),
            "macro_f1": float(f1_score(y_true, y_pred, labels=list(CLASSES), average="macro", zero_division=0)),
            "predicted_counts": {label: int(np.sum(np.asarray(y_pred) == label)) for label in CLASSES}}
    pooled_trades = {}
    for name, rows in all_trade_metrics.items():
        n = sum(r["non_overlapping_trades"] for r in rows)
        dollars = sum(r["net_pnl_usd"] for r in rows)
        turnover = sum(r["traded_notional_usd"] for r in rows)
        bps_values = [r["mean_net_bps_per_trade"] * r["non_overlapping_trades"] for r in rows
                      if r["mean_net_bps_per_trade"] is not None]
        pooled_trades[name] = {"non_overlapping_trades": n,
            "mean_net_bps_per_trade": sum(bps_values) / n if n else None,
            "win_rate_pct": (sum(r["win_rate_pct"] * r["non_overlapping_trades"] / 100 for r in rows
                if r["win_rate_pct"] is not None) / n * 100) if n else None,
            "net_pnl_usd": dollars, "traded_notional_usd": turnover,
            "net_bps_per_round_trip_turnover": dollars / turnover * 10_000 if turnover else None}
    result = {"study": "LOBSTER executable-target order-flow ML benchmark",
        "data": "Official LOBSTER five-level event samples, 2012-06-21, five US stocks",
        "source_files": [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files],
        "protocol": {"event_stride": event_stride, "order_size_shares": SIZE,
            "label": "Choose up/down only if the better fully filled 100-share displayed-book round trip earns positive net bps after 1 bp per side; otherwise flat.",
            "horizon": "First sampled book at or after 1 second, capped at 2 seconds.",
            "split": "For each held-out stock, train on the first 70% of each training stock's session; test on the held-out stock's final 20%; leave a 10% time gap. Training labels must end before the training cutoff.",
            "features": list(FEATURES), "models": list(specs),
            "trading_metrics": "Test predictions are executed only when directional and only if the prior simulated round trip for that ticker has ended; displayed-depth marketable entry and exit, overlapping trades excluded."},
        "usable_samples_by_ticker": {ticker: len(rows) for ticker, rows in stocks.items()},
        "folds": fold_reports, "pooled_scores": pooled_scores, "pooled_non_overlapping_policy_results": pooled_trades,
        "limitations": ["One historical day cannot establish regime robustness; no date-level uncertainty interval is reported.",
            "LOBSTER provides five displayed levels; samples lacking full 100-share entry and exit depth are excluded from labels.",
            "The target uses static displayed depth and illustrative 1 bp/side fees; it omits impact, replenishment, queue position, and hidden liquidity.",
            "This is a research benchmark, not evidence of profitability or a deployable strategy."]}
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    lines = ["# LOBSTER Executable-Target ML Study", "", "## Question and protocol", "",
        "Can book state and recent order flow help identify whether a 100-share long or short round trip has positive displayed-book P&L after fees, or whether the model should stay flat? Entry and exit sweeps must both fully fill from the sampled top five levels. The target uses the first sampled future book at 1–2 seconds and deducts an assumed 1 bp fee per side.", "",
        "For each fold, models train on the first 70% of the session for four stocks and test on the final 20% of the held-out fifth stock; the 10% middle block is unused. Training examples whose target reaches beyond the training cutoff are removed. Parameters are fixed and no test-period tuning is done. The experiment is one day, so temporal separation is meaningful within that session but is not multi-date validation.", "",
        "Features combine top-five depth imbalance, spread, depth scale, signed add/cancel/execution flow over the previous 100 events, one-second return, and one-second realized volatility. Models are a training-prior baseline, logistic regression, histogram gradient boosting, a boosted-tree no-flow ablation, and a book-only boosted tree.", "",
        "## Pooled held-out classification", "", "| Model | Balanced accuracy | Accuracy | Macro F1 | Predicted up / flat / down | Non-overlap trades | Mean net bps/trade | Net P&L ($) | Net bps / round-trip turnover |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in pooled_scores.items():
        trades = pooled_trades[name]
        counts = row["predicted_counts"]
        mean_bps = trades["mean_net_bps_per_trade"]
        turnover_bps = trades["net_bps_per_round_trip_turnover"]
        lines.append(f"| {name} | {row['balanced_accuracy']:.3f} | {row['accuracy']:.3f} | {row['macro_f1']:.3f} | {counts['up']} / {counts['flat']} / {counts['down']} | {trades['non_overlapping_trades']} | {_fmt_number(mean_bps)} | ${trades['net_pnl_usd']:.2f} | {_fmt_number(turnover_bps)} |")
    lines.extend(["", "## Held-out stock folds", "", "| Stock | Train rows | Test rows | Test labels up / flat / down | Prior BA | Logistic BA | HGB BA | HGB without flow BA | Book-only HGB BA |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for fold in fold_reports:
        s = fold["scores"]
        ycounts = s["prior_baseline"]["test_target_counts"]
        lines.append(f"| {fold['heldout_ticker']} | {fold['train_rows']:,} | {fold['test_rows']:,} | {ycounts['up']} / {ycounts['flat']} / {ycounts['down']} | {s['prior_baseline']['balanced_accuracy']:.3f} | {s['logistic_regression']['balanced_accuracy']:.3f} | {s['hist_gradient_boosting']['balanced_accuracy']:.3f} | {s['hgb_without_order_flow']['balanced_accuracy']:.3f} | {s['hgb_book_only']['balanced_accuracy']:.3f} |")
    lines.extend(["", "## Limits", "", "A fully executable target is stricter than midpoint direction, but it also selects observations where the displayed top five levels can fill both sides. Static sweeps assume no impact or replenishment. The 2012 sample covers one day; this is an exploratory, time-ordered held-out-stock check, not a claim about current markets or a production strategy. No date-based confidence interval is possible from a single session. See the JSON artifact for per-fold metrics and assumptions.", "", "Reproduce with `python lobster_executable_ml.py` after downloading the official LOBSTER sample files described in `data/README.md`.", ""])
    output_report.parent.mkdir(parents=True, exist_ok=True)
    output_report.write_text("\n".join(lines), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, action="append", dest="data_dirs")
    parser.add_argument("--event-stride", type=int, default=EVENT_STRIDE)
    parser.add_argument("--output-json", type=Path, default=ROOT / "results" / "lobster_executable_ml_metrics.json")
    parser.add_argument("--output-report", type=Path, default=ROOT / "results" / "lobster_executable_ml_study.md")
    args = parser.parse_args()
    result = run(tuple(args.data_dirs or DEFAULT_DIRS), args.output_json, args.output_report, args.event_stride)
    print(json.dumps({"usable_samples_by_ticker": result["usable_samples_by_ticker"],
        "pooled_scores": result["pooled_scores"], "pooled_policy_results": result["pooled_non_overlapping_policy_results"],
        "outputs": [str(args.output_json), str(args.output_report)]}, indent=2))


if __name__ == "__main__":
    main()
