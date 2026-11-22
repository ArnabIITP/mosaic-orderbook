# WSELOB Multi-Session Executable-Outcome Study

## Research question

Can a model trained on four Warsaw Stock Exchange instruments identify, on an unseen fifth instrument, when backward-looking book features correspond to a positive hypothetical marketable round trip after displayed spread/depth costs and fees?

## Data and protocol

The study reconstructs 85,846,918 updates and evaluates 621,114 full-depth-eligible samples over 250 sessions and five instruments from WSELOB-2017. Each prediction is evaluated at the first same-session sampled book at least 60 seconds later, capped at 120 seconds. The direction label is the better of long or short only if its hypothetical net round trip is positive after 2.00 bps round-trip fees; otherwise it is flat.

For each 500-share intent, both entry and exit prices are swept through up to ten displayed levels. Rows require full visible size on both sides at both times. The first 70% of sessions train, the middle 15% are reserved and unused, and the final 15% are held out. For each held-out instrument, that instrument is excluded from all fitting. Settings are fixed; no test-period tuning is performed.

The independent data-author reference implementation matched MOSAIC's reconstructed top-ten books at 27,582 sampled states across the first 100,000 events of nine dates for each of the five stocks, with zero mismatches. This improves confidence in the reconstruction but is not exhaustive validation of all 85.8 million updates. [Expanded validation metrics](wselob_multi_stock_reference_validation_100k.json)

## Held-out results

| Unseen ticker | Test sessions | Eligible rows | Target flat / down / up | Model | Balanced accuracy | Predicted flat / down / up | Sequential round trips | Mean net bps/trip | Date-bootstrap 95% interval |
|---|---:|---:|---:|---|---:|---|---:|---:|---:|
| KGHM | 38 | 17,626 | 15,535 / 1,106 / 985 | majority_baseline | 0.333 | 17,626 / 0 / 0 | 0 | n/a | n/a |
| KGHM | 38 | 17,626 | 15,535 / 1,106 / 985 | logistic_regression | 0.333 | 17,600 / 0 / 26 | 9 | -40.31 | [-47.06, -33.10] |
| KGHM | 38 | 17,626 | 15,535 / 1,106 / 985 | hist_gradient_boosting | 0.334 | 17,621 / 0 / 5 | 2 | 5.95 | [-7.02, 18.92] |
| PEKAO | 38 | 7,363 | 6,537 / 431 / 395 | majority_baseline | 0.333 | 7,363 / 0 / 0 | 0 | n/a | n/a |
| PEKAO | 38 | 7,363 | 6,537 / 431 / 395 | logistic_regression | 0.333 | 7,359 / 0 / 4 | 2 | -19.56 | [-33.34, -5.77] |
| PEKAO | 38 | 7,363 | 6,537 / 431 / 395 | hist_gradient_boosting | 0.333 | 7,361 / 0 / 2 | 2 | -23.13 | [-29.62, -16.65] |
| PKOBP | 38 | 17,435 | 14,425 / 1,276 / 1,734 | majority_baseline | 0.333 | 17,435 / 0 / 0 | 0 | n/a | n/a |
| PKOBP | 38 | 17,435 | 14,425 / 1,276 / 1,734 | logistic_regression | 0.334 | 17,433 / 1 / 1 | 1 | 7.77 | [7.77, 7.77] |
| PKOBP | 38 | 17,435 | 14,425 / 1,276 / 1,734 | hist_gradient_boosting | 0.333 | 17,429 / 5 / 1 | 3 | -6.83 | [-18.56, 1.61] |
| PKNORLEN | 38 | 20,612 | 17,088 / 1,856 / 1,668 | majority_baseline | 0.333 | 20,612 / 0 / 0 | 0 | n/a | n/a |
| PKNORLEN | 38 | 20,612 | 17,088 / 1,856 / 1,668 | logistic_regression | 0.333 | 20,601 / 3 / 8 | 5 | -35.13 | [-46.94, -23.33] |
| PKNORLEN | 38 | 20,612 | 17,088 / 1,856 / 1,668 | hist_gradient_boosting | 0.334 | 20,572 / 1 / 39 | 11 | -22.13 | [-39.04, -1.47] |
| PZU | 38 | 18,967 | 15,693 / 1,583 / 1,691 | majority_baseline | 0.333 | 18,967 / 0 / 0 | 0 | n/a | n/a |
| PZU | 38 | 18,967 | 15,693 / 1,583 / 1,691 | logistic_regression | 0.333 | 18,963 / 0 / 4 | 2 | -30.16 | [-86.37, 26.05] |
| PZU | 38 | 18,967 | 15,693 / 1,583 / 1,691 | hist_gradient_boosting | 0.334 | 18,962 / 3 / 2 | 3 | -5.36 | [-19.25, 3.18] |

### Pooled sequential held-out policy outcomes

| Model | Non-overlapping round trips | Active sessions | Mean net bps/trip | Equal-weight daily mean (bps) | Session-bootstrap 95% interval | Profitable trips |
|---|---:|---:|---:|---:|---:|---:|
| majority_baseline | 0 | 0 | n/a | n/a | n/a | n/a |
| logistic_regression | 19 | 14 | -33.16 | -34.48 | [-44.23, -24.41] | 10.5% |
| hist_gradient_boosting | 21 | 16 | -14.97 | -11.66 | [-20.63, -3.94] | 28.6% |

## Interpretation

The classifiers are effectively at the three-class prior baseline and mostly predict flat. The boosted policy's 21 selected sequential round trips averaged −14.97 bps per trade; the equal-weight mean across its 16 active sessions was −11.66 bps (95% session-bootstrap interval −20.63 to −3.94). This is evidence against the tested feature/model/policy combination under the stated assumptions, not evidence that market microstructure signals are impossible. The small number of selected trades and active sessions means the estimated loss should not be generalized beyond this test. Read the per-ticker rows alongside the pooled diagnostic.

## Limitations

- WSELOB contains order-book updates but no independent trade-print stream in this project; it cannot validate passive executions, queue-ahead depletion, or fill adverse selection.
- Entry and exit assume immediate access to displayed depth at sampled timestamps. Arrival delay, intervening events, hidden liquidity, replenishment, market impact, and interaction with the order are not modeled.
- Full-size displayed-depth eligibility selects the sample and excludes orders that could not be fully swept in the visible book.
- The data cover five WSE names in 2017 only; results do not establish behavior in other venues, instruments, or current markets.
- The sequential policy allows one open simulated position per ticker but does not include capital allocation, portfolio risk limits, or live order handling.

## Reproduction

From the repository root, with the five WSELOB files installed as described in [data/README.md](../data/README.md):

```powershell
python multistock_executable_study.py --data-dir data/wselob_2017 --stride 100 --order-size 500 --fee-bps-per-side 1
```

The output JSON records per-input SHA-256 hashes, row counts, splits, class balance, model metrics, simulated trade outcomes, and the limitations.
