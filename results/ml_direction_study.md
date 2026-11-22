# WSELOB PEKAO Mid-Price Direction Benchmark

## Research question

Can a snapshot of the displayed book and recent price history classify the next sampled mid-price as down, unchanged, or up about one minute later? The label is price direction, not a simulated trade return.

## Data and target

The benchmark uses 58,426 labeled snapshots from the 2017 PEKAO study (94,390 source snapshots before target eligibility filtering). The target is the first sampled quote at or after 60 seconds, provided it arrives within 120 seconds and on the same trading date. The realized target delay has median 79.2 seconds and 95th percentile 114.2 seconds.

Label counts: down 26,542, flat 5,875, up 26,009.

## Evaluation design

Sessions are sorted chronologically and assigned as whole dates: 70% training, 15% validation, 15% test. No rows are shuffled across dates. Feature imputation, scaling, and model fitting use training dates only. The validation set is a forward-time diagnostic; model settings were fixed before evaluating the final test block.

- Train: 175 dates (20170102–20170912), 44,423 labeled snapshots.
- Validation: 37 dates (20170913–20171103), 6,639 labeled snapshots.
- Test: 38 dates (20171106–20171229), 7,364 labeled snapshots.

The feature set uses spread, bid/ask depth imbalance and depth scales, trailing one-minute realized volatility, and backward-looking 30/120/300-second midpoint returns. It excludes the current mid-price level and all future values. The comparison is a training-prior majority baseline, regularized logistic regression, and a fixed-configuration histogram gradient-boosting classifier.

## Held-out results

| Model | Accuracy | Balanced accuracy | Macro F1 | Log loss |
|---|---:|---:|---:|---:|
| Training-prior baseline | 0.443 | 0.333 | 0.205 | 0.966 |
| Logistic regression | 0.436 | 0.327 | 0.300 | 0.967 |
| Histogram gradient boosting | 0.467 | 0.350 | 0.329 | 0.968 |

The boosted tree reaches 35.01% balanced accuracy versus 33.33% for the majority baseline. In a paired 2,000-resample bootstrap over test dates, the lift is 1.68 percentage points (95% interval 0.74 to 2.71). Its log loss is slightly worse than the baseline; logistic regression also fails to improve balanced accuracy.

## Interpretation and limits

The lift is small and comes from one stock in one historical year. It may not persist on another instrument or in current data. Snapshots are sampled every 100 source events, so the quote used for the one-minute label arrives later: the median delay is 79.2 seconds and the 95th percentile is 114.2 seconds. No trading rule or net-return calculation is tested. Forward labels overlap within each date; the chronological split keeps dates separate, and the bootstrap resamples whole test dates, but observations within a date remain dependent.

The separate [leave-one-stock-out study](multi_stock_ml_study.md) now tests transfer to four unseen WSE names using the same 2017 source. It finds a small positive balanced-accuracy lift across the five held-out stocks, but the [PEKAO cost-aware study](execution_aware_study.md) still shows negative average markout after its illustrative costs.
