# PEKAO Execution-Aware Direction Benchmark

## Question

When the direction classifier calls the next sampled midpoint up or down, does that call remain favorable after crossing the displayed book to enter and exit a 500-share position? This is a conditional markout diagnostic, not a portfolio backtest.

## Data and protocol

The experiment uses 58,426 eligible snapshots from 94,390 source rows. The first same-session quote at or after 60 seconds is used if it arrives within 120 seconds; realized delay is 79.2 seconds median and 114.2 seconds at the 95th percentile. The final 15% of trading dates are held out. Models are trained only on the earlier 70% of dates; the middle 15% is reserved for validation and does not tune this fixed comparison.

For each test snapshot, a hypothetical 500-share market order enters at the displayed buy or sell sweep VWAP. At the target snapshot, it exits with the opposite-side sweep VWAP. The calculation requires a full displayed 500-share fill on both sides at both snapshots and deducts the stated fee on entry and exit. Costs and future prices are normalized to the decision midpoint.

## Held-out outcomes

| Signal rule | Calls (up/down/flat) | Active dates | Mean net markout (bps/trade) | 95% date-bootstrap interval | Profitable trades |
|---|---:|---:|---:|---:|---:|
| Training-prior majority class | 0/7,364/0 | 38 | -17.04 | [-18.01, -16.12] | 5.9% |
| Logistic regression | 2,508/4,856/0 | 38 | -17.27 | [-18.25, -16.39] | 5.6% |
| Histogram gradient boosting | 3,314/4,050/0 | 38 | -16.50 | [-17.43, -15.67] | 6.2% |
| Depth-imbalance sign | 3,428/3,934/2 | 38 | -17.34 | [-18.37, -16.40] | 5.5% |

The boosted tree improves the point estimate over the majority rule by 0.38 bps per date; its paired date-bootstrap 95% interval is [-0.27, 0.95]. All classifier predictions were directional on this test block (none predicted flat), so this is a full-coverage diagnostic rather than a selective signal policy.

The rule set includes the prior-only majority classifier, logistic regression, histogram gradient boosting, and a simple top-book depth-imbalance sign rule. The models use the same nine backward-looking features and chronological date split as the direction benchmark. The bootstrap resamples whole active test dates and weights by the number of signals on each sampled date.

## Interpretation and limits

All four rules have negative mean net markout after the stated fees. Signal windows overlap, so these rows are not independent trades and cannot be summed into portfolio returns. The study assumes immediate complete fills at static displayed depth, does not model queue position or intervening book changes, and uses an illustrative 1 bp fee per side. The sample is one stock in one historical year; quotes are sampled every 100 source events, making this unsuitable for claiming seconds-level HFT performance. No capital, inventory, or risk limits are simulated.

The intended result is a cost-aware quality check on the direction predictions, not a claim of profitability. See the JSON metrics for source hash, date splits, coverage and model details.
