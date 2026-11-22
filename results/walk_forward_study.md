# PEKAO Walk-Forward Direction Study

## Protocol

The fixed direction benchmark is evaluated on 5 chronological test blocks. Each fold trains on an expanding history, leaves 5 full sessions between train and test, and scores the next 25 sessions. Labels use same-session future midpoints at or after 60 seconds, capped at 120 seconds. Whole-session splits keep overlapping within-session labels from crossing split boundaries.

Each model is refit from scratch in each fold. Hyperparameters stay fixed; no tuning occurs on the reported test blocks. Five complete sessions are embargoed between train and test. Confidence intervals resample held-out dates and compare each model to the training-prior baseline.

## Fold results

| Fold | Train sessions | Embargo | Test sessions | Test rows | Prior BA | Logistic BA | Boosted BA | Boosted log loss |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100 (20170102–20170526) | 5 | 25 (20170605–20170710) | 6,916 | 0.333 | 0.341 | 0.346 | 0.962 |
| 2 | 130 (20170102–20170710) | 5 | 25 (20170718–20170822) | 3,420 | 0.333 | 0.344 | 0.354 | 0.976 |
| 3 | 160 (20170102–20170822) | 5 | 25 (20170830–20171003) | 3,642 | 0.333 | 0.330 | 0.371 | 0.979 |
| 4 | 190 (20170102–20171003) | 5 | 25 (20171011–20171115) | 5,400 | 0.333 | 0.335 | 0.357 | 0.996 |
| 5 | 220 (20170102–20171115) | 5 | 25 (20171123–20171229) | 4,625 | 0.333 | 0.321 | 0.350 | 0.950 |

Pooled out-of-fold balanced accuracy: prior 0.333, logistic 0.335, boosted 0.354. The boosted-minus-prior date-bootstrap 95% interval is [0.015, 0.026].

## Feature ablations

The same fixed tree is refit after removing one feature family. These are descriptive ablations, not retuned models.

| Feature set | Pooled out-of-fold BA | Lift vs prior | Date-bootstrap 95% lift interval |
|---|---:|---:|---:|
| All features | 0.354 | +0.021 | [+0.015, +0.026] |
| Without recent returns | 0.340 | +0.007 | [+0.002, +0.012] |
| Without book features | 0.354 | +0.021 | [+0.014, +0.027] |
| Without volatility | 0.353 | +0.020 | [+0.014, +0.026] |

## Limits

This measures midpoint classification, not executable P&L. The folds cover one WSE instrument and one historical year; an embargo reduces near-boundary overlap but does not eliminate market-regime dependence. Feature ablations are correlated comparisons and are not causal attribution. See the queue-aware sensitivity and cost-aware markout reports before drawing trading conclusions.

Reproduce after running `python analyze_wselob.py --stride 100 --fee-bps 1`:

```powershell
python walk_forward_benchmark.py
```
