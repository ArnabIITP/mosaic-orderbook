# WSELOB Leave-One-Stock-Out Direction Study

## Question

Do order-book features learned from four WSE stocks transfer to a fifth stock that the model has never seen? This is a stricter generalization check than randomly splitting snapshots from the same instrument.

## Protocol

The study reconstructs 817,626 usable snapshots from 85,846,918 updates across five WSE instruments in 2017. For each fold, the held-out stock is excluded from all model fitting. Models train on the other four stocks’ first 175 dates and are evaluated on the held-out stock’s final 38 dates. The intervening 37 dates are reserved and are not used to tune the fixed settings. Target construction uses the first same-session midpoint at or after 60 seconds, capped at 120 seconds.

Training rows receive weights so each of the four training stocks has equal total influence. The comparison uses a training-prior baseline, regularized logistic regression, and histogram gradient boosting with the feature set and fixed settings from the PEKAO study. Confidence intervals resample complete held-out test dates.

## Held-out results

| Unseen stock | Test rows | Target delay median / p95 | Majority balanced accuracy | Logistic balanced accuracy | Boosted balanced accuracy | Boosted lift vs majority (95% date CI) | Boosted log loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| KGHM | 17,626 | 76.5s / 112.3s | 0.333 | 0.323 | 0.349 | +1.58 pp [+0.83, +2.43] | 0.991 |
| PEKAO | 7,364 | 79.7s / 114.5s | 0.333 | 0.330 | 0.354 | +2.05 pp [+0.97, +3.22] | 0.964 |
| PKOBP | 17,436 | 77.0s / 113.3s | 0.333 | 0.335 | 0.366 | +3.31 pp [+2.54, +3.99] | 0.940 |
| PKNORLEN | 20,613 | 75.9s / 112.3s | 0.333 | 0.316 | 0.363 | +2.97 pp [+2.29, +3.70] | 0.952 |
| PZU | 18,967 | 76.3s / 112.5s | 0.333 | 0.318 | 0.373 | +3.95 pp [+3.04, +4.86] | 0.904 |

Across the five held-out stocks, the unweighted mean boosted-tree lift is +2.77 percentage points. Read the ticker-level results as the main evidence: this five-stock universe is small, the data are from one historical year, and the results do not establish stability in other markets or later periods.

## Limits

Snapshots are sampled every 100 source events, so the quote at a requested 60-second horizon arrives later and with instrument-dependent delay. This evaluates midpoint direction only; it does not establish positive returns after spread, fees, queue position, latency, or impact. The separate execution-aware PEKAO study finds negative average markout after its illustrative costs. The source data are historical and licensed CC BY 4.0; do not redistribute raw files with the software repository.

## Reproduce

Download and extract the five WSELOB order-book HDF5 files as described in [data/README.md](../data/README.md), then run:

```powershell
python multi_stock_ml_benchmark.py --data-dir data/wselob_2017 --stride 100
```

The script verifies and caches sampled observations locally under `build/`. Source file sizes and SHA-256 hashes, reconstruction counters, fold metrics, class counts, and date-bootstrap intervals are in the companion JSON.
