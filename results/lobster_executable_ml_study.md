# LOBSTER Executable-Target ML Study

## Question and protocol

Can book state and recent order flow help identify whether a 100-share long or short round trip has positive displayed-book P&L after fees, or whether the model should stay flat? Entry and exit sweeps must both fully fill from the sampled top five levels. The target uses the first sampled future book at 1–2 seconds and deducts an assumed 1 bp fee per side.

For each fold, models train on the first 70% of the session for four stocks and test on the final 20% of the held-out fifth stock; the 10% middle block is unused. Training examples whose target reaches beyond the training cutoff are removed. Parameters are fixed and no test-period tuning is done. The experiment is one day, so temporal separation is meaningful within that session but is not multi-date validation.

Features combine top-five depth imbalance, spread, depth scale, signed add/cancel/execution flow over the previous 100 events, one-second return, and one-second realized volatility. Models are a training-prior baseline, logistic regression, histogram gradient boosting, a boosted-tree no-flow ablation, and a book-only boosted tree.

## Pooled held-out classification

| Model | Balanced accuracy | Accuracy | Macro F1 | Predicted up / flat / down | Non-overlap trades | Mean net bps/trade | Net P&L ($) | Net bps / round-trip turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| prior_baseline | 0.333 | 0.981 | 0.330 | 0 / 1081 / 0 | 0 | n/a | $0.00 | n/a |
| logistic_regression | 0.359 | 0.981 | 0.378 | 0 / 1080 / 1 | 1 | 0.59 | $3.43 | 0.30 |
| hist_gradient_boosting | 0.331 | 0.974 | 0.329 | 6 / 1074 / 1 | 6 | -4.05 | $-23.27 | -1.63 |
| hgb_without_order_flow | 0.330 | 0.969 | 0.328 | 11 / 1069 / 1 | 9 | -4.08 | $-9.87 | -2.03 |
| hgb_book_only | 0.332 | 0.976 | 0.329 | 5 / 1076 / 0 | 3 | -3.25 | $-2.60 | -1.62 |

## Held-out stock folds

| Stock | Train rows | Test rows | Test labels up / flat / down | Prior BA | Logistic BA | HGB BA | HGB without flow BA | Book-only HGB BA |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AAPL | 1,810 | 119 | 3 / 112 / 4 | 0.333 | 0.417 | 0.330 | 0.333 | 0.333 |
| AMZN | 1,957 | 15 | 0 / 14 / 1 | 0.333 | 0.333 | 0.333 | 0.333 | 0.333 |
| GOOG | 1,941 | 3 | 0 / 3 / 0 | 0.333 | 0.333 | 0.333 | 0.333 | 0.333 |
| INTC | 1,056 | 449 | 2 / 442 / 5 | 0.333 | 0.333 | 0.329 | 0.325 | 0.330 |
| MSFT | 1,148 | 495 | 3 / 489 / 3 | 0.333 | 0.333 | 0.333 | 0.333 | 0.333 |

## Limits

A fully executable target is stricter than midpoint direction, but it also selects observations where the displayed top five levels can fill both sides. Static sweeps assume no impact or replenishment. The 2012 sample covers one day; this is an exploratory, time-ordered held-out-stock check, not a claim about current markets or a production strategy. No date-based confidence interval is possible from a single session. See the JSON artifact for per-fold metrics and assumptions.

Reproduce with `python lobster_executable_ml.py` after downloading the official LOBSTER sample files described in `data/README.md`.
