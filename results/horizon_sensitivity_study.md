# PEKAO Direction Model Horizon Sensitivity

## Question

Does the held-out direction-classification result change when the same feature set and calendar-date split are evaluated at different forward horizons? This is a sensitivity study; no horizon is selected from its test score.

## Protocol

The study uses 94,390 sampled snapshots from one PEKAO instrument in 2017. For each horizon, the target is the first same-session sampled midpoint at or after that horizon, with a maximum delay of twice the requested horizon. All horizons use the same chronological date blocks: 175 train dates, 37 validation dates, and 38 final test dates. Models are refit independently on each horizon using the existing training-only preprocessing and fixed model settings.

## Held-out results

| Requested horizon | Labeled rows (% source) | Realized delay median / p95 | Test rows | Boosted-tree balanced accuracy | Lift vs majority (95% paired date CI) | Log-loss change vs majority |
|---:|---:|---:|---:|---:|---:|---:|
| 5s | 12,515 (13.3%) | 7.1s / 9.7s | 1,473 | 0.359 | +2.60 pp [+0.58, +4.82] | +0.018 |
| 10s | 20,562 (21.8%) | 14.0s / 19.3s | 2,434 | 0.360 | +2.64 pp [+1.18, +4.35] | +0.010 |
| 30s | 40,230 (42.6%) | 40.9s / 57.6s | 4,880 | 0.358 | +2.46 pp [+1.23, +3.69] | +0.000 |
| 60s | 58,426 (61.9%) | 79.2s / 114.2s | 7,364 | 0.350 | +1.68 pp [+0.74, +2.71] | +0.002 |

## Interpretation and limits

Shorter horizons have much lower row coverage because snapshots are sampled every 100 source events. Those rows disproportionately come from periods with faster event activity; they are not a representative high-frequency sample. Realized target delay can materially exceed the requested horizon, as shown in the table. The 5-second and 10-second results therefore describe this selected sample, not a reliable seconds-level HFT forecast.

A balanced-accuracy lift does not imply calibrated probabilities or positive net returns. The separate [execution-aware markout study](execution_aware_study.md) evaluates the 60-second calls after a displayed-book round trip and finds negative mean markout after the stated costs. This experiment does not evaluate passive order fills, queue position, a portfolio, or current market conditions.

All date blocks, row counts, class counts, and per-model metrics are recorded in the companion JSON.
