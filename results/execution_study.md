# Historical Order Book Execution Study

## Executive summary

The study replays hypothetical market orders against **173,576 snapshots from five NASDAQ stocks** on June 21, 2012. Liquidity and volatility buckets are compared within each ticker at fixed order sizes. The sample is one historical day, so the results are descriptive and do not establish causality or forecast current execution.

## Data and research context

- **Data:** Official LOBSTER five-level samples for AMZN, AAPL, GOOG, INTC, MSFT, June 21, 2012, 09:30–16:00 US Eastern. Event counts by ticker: AMZN 155,935, AAPL 301,587, GOOG 112,673, INTC 581,030, MSFT 595,800. Every 10th aligned event/book snapshot was analyzed.
- **Source:** [Official LOBSTER sample files](https://php.lobsterdata.com/info/DataSamples.php). LOBSTER documents that each message row corresponds to the resulting order-book row, with price recorded in 1/10,000 USD and sizes in shares; see [output format](https://php.lobsterdata.com/info/DataStructure.php).
- **Paper:** Xiao et al. (2025), [LiT: limit order book transformer](https://doi.org/10.3389/frai.2025.1616485), studies short-horizon market-movement forecasting from Binance Level 2 data. It motivates analysis of changing book states but does not evaluate this simulator or this execution question. This report covers matching and execution; the separate PEKAO study tests a short-horizon direction benchmark.

## Method

For each ticker, the analyzer samples every 10th event, excludes crossed/invalid or empty quotes, computes volatility from the sampled midpoint path, and drops the first 60 seconds so every volatility estimate has a complete trailing window. It then replays hypothetical buy orders against the five displayed ask levels and sells against the five bid levels. Order sizes are 100, 500, and 1,000 shares. VWAP shortfall is measured against the snapshot midpoint in basis points; positive values indicate immediate execution cost. Fill rate is filled shares divided by requested shares, and unavailable depth remains unfilled.

Within each ticker, buy liquidity buckets use terciles of displayed ask depth and sell liquidity buckets use bid depth, each across the top five levels. Volatility buckets use terciles of trailing one-minute realised volatility from sampled midpoints, using observations at or before each snapshot. Results are grouped by ticker so cross-stock depth differences do not define the buckets. The 500-share order is compared across buckets. These are observational comparisons: spread, time of day, and other market conditions can confound the relationships. Snapshot replay assumes immediate access to all displayed size; it omits latency, queue priority, fees, hidden liquidity, replenishment, and market impact.

Each snapshot is executed through the C++17 order book at both sides and all three sizes. The results are compared against the Python sweep implementation on the exact same snapshot set before reporting; see the engine validation record in `lobster_metrics.json`. The timing values are diagnostic and not a clean speed ratio: the C++ value includes its CLI process and input/output transfer.

## Overall results

| ticker | side | order_shares | mean_slippage_bps | mean_fill_rate |
|---|---|---|---|---|
| AMZN | buy | 100 | 3.345 | 0.996 |
| AMZN | sell | 100 | 3.266 | 0.999 |
| AMZN | buy | 500 | 4.542 | 0.848 |
| AMZN | sell | 500 | 4.568 | 0.911 |
| AMZN | buy | 1000 | 4.815 | 0.558 |
| AMZN | sell | 1000 | 4.930 | 0.660 |
| AAPL | buy | 100 | 1.490 | 0.997 |
| AAPL | sell | 100 | 1.462 | 0.999 |
| AAPL | buy | 500 | 2.121 | 0.867 |
| AAPL | sell | 500 | 2.165 | 0.920 |
| AAPL | buy | 1000 | 2.260 | 0.587 |
| AAPL | sell | 1000 | 2.361 | 0.665 |
| GOOG | buy | 100 | 2.860 | 0.990 |
| GOOG | sell | 100 | 2.903 | 0.995 |
| GOOG | buy | 500 | 4.086 | 0.722 |
| GOOG | sell | 500 | 3.944 | 0.804 |
| GOOG | buy | 1000 | 4.226 | 0.409 |
| GOOG | sell | 1000 | 4.073 | 0.520 |
| INTC | buy | 100 | 2.293 | 1.000 |
| INTC | sell | 100 | 2.293 | 1.000 |
| INTC | buy | 500 | 2.318 | 1.000 |
| INTC | sell | 500 | 2.315 | 1.000 |
| INTC | buy | 1000 | 2.348 | 1.000 |
| INTC | sell | 1000 | 2.338 | 1.000 |
| MSFT | buy | 100 | 2.051 | 1.000 |
| MSFT | sell | 100 | 2.050 | 1.000 |
| MSFT | buy | 500 | 2.073 | 1.000 |
| MSFT | sell | 500 | 2.068 | 1.000 |
| MSFT | buy | 1000 | 2.092 | 1.000 |
| MSFT | sell | 1000 | 2.086 | 1.000 |

Slippage generally rises with order size and fill rates fall as the request consumes more than the five displayed levels. See per-ticker summaries below; pooled values above weight each ticker by its sampled event count.

## Liquidity terciles (500-share order)

| ticker | side | bucket | observations | mean_spread_cents | median_executable_side_depth_shares | slippage_bps | fill_rate |
|---|---|---|---|---|---|---|---|
| AMZN | buy | thin | 5188 | 12.935 | 298.000 | 4.458 | 0.575 |
| AMZN | buy | middle | 5193 | 13.858 | 534 | 4.946 | 0.970 |
| AMZN | buy | deep | 5142 | 13.384 | 854.000 | 4.218 | 1.000 |
| AMZN | sell | thin | 5181 | 14.217 | 377 | 5.155 | 0.733 |
| AMZN | sell | middle | 5175 | 13.515 | 646 | 4.753 | 1.000 |
| AMZN | sell | deep | 5167 | 12.443 | 1128 | 3.795 | 1.000 |
| AAPL | buy | thin | 10021 | 16.176 | 334 | 2.232 | 0.619 |
| AAPL | buy | middle | 10016 | 15.820 | 543.000 | 2.265 | 0.982 |
| AAPL | buy | deep | 10003 | 14.840 | 1000 | 1.867 | 1.000 |
| AAPL | sell | thin | 10081 | 17.329 | 409 | 2.591 | 0.762 |
| AAPL | sell | middle | 9970 | 15.399 | 629.000 | 2.184 | 1.000 |
| AAPL | sell | deep | 9989 | 14.093 | 1213 | 1.718 | 1.000 |
| GOOG | buy | thin | 3734 | 30.153 | 229.000 | 3.735 | 0.420 |
| GOOG | buy | middle | 3714 | 28.859 | 365.000 | 4.083 | 0.754 |
| GOOG | buy | deep | 3721 | 29.654 | 575 | 4.442 | 0.992 |
| GOOG | sell | thin | 3729 | 32.607 | 261 | 4.401 | 0.516 |
| GOOG | sell | middle | 3717 | 29.766 | 440 | 4.308 | 0.896 |
| GOOG | sell | deep | 3723 | 26.292 | 879 | 3.122 | 1.000 |
| INTC | buy | thin | 19154 | 1.234 | 58538.000 | 2.334 | 1.000 |
| INTC | buy | middle | 19154 | 1.211 | 80404.500 | 2.243 | 1.000 |
| INTC | buy | deep | 19154 | 1.275 | 96080.000 | 2.376 | 1.000 |
| INTC | sell | thin | 19155 | 1.221 | 65808 | 2.292 | 1.000 |
| INTC | sell | middle | 19154 | 1.218 | 86894.500 | 2.267 | 1.000 |
| INTC | sell | deep | 19153 | 1.280 | 108984 | 2.385 | 1.000 |
| MSFT | buy | thin | 19794 | 1.210 | 69804.000 | 2.029 | 1.000 |
| MSFT | buy | middle | 19797 | 1.195 | 84817 | 1.963 | 1.000 |
| MSFT | buy | deep | 19791 | 1.350 | 101056 | 2.227 | 1.000 |
| MSFT | sell | thin | 19794 | 1.230 | 71139.500 | 2.049 | 1.000 |
| MSFT | sell | middle | 19800 | 1.229 | 87220.000 | 2.018 | 1.000 |
| MSFT | sell | deep | 19788 | 1.296 | 104509.500 | 2.137 | 1.000 |

## Trailing one-minute volatility terciles (500-share order)

| ticker | bucket | observations | mean_spread_cents | median_two_sided_depth_shares | buy_slippage_bps | buy_fill_rate | sell_slippage_bps | sell_fill_rate |
|---|---|---|---|---|---|---|---|---|
| AMZN | low | 5178 | 12.153 | 1185.000 | 4.060 | 0.817 | 3.979 | 0.912 |
| AMZN | middle | 5173 | 13.088 | 1249 | 4.354 | 0.846 | 4.292 | 0.918 |
| AMZN | high | 5172 | 14.937 | 1228.000 | 5.212 | 0.881 | 5.434 | 0.903 |
| AAPL | low | 10014 | 13.274 | 1298.000 | 1.827 | 0.857 | 1.842 | 0.943 |
| AAPL | middle | 10014 | 15.060 | 1294.000 | 2.049 | 0.859 | 2.053 | 0.929 |
| AAPL | high | 10012 | 18.504 | 1320.500 | 2.487 | 0.884 | 2.601 | 0.889 |
| GOOG | low | 3723 | 25.956 | 866 | 3.366 | 0.695 | 3.369 | 0.821 |
| GOOG | middle | 3723 | 27.334 | 876 | 3.635 | 0.710 | 3.577 | 0.818 |
| GOOG | high | 3723 | 35.378 | 860 | 5.259 | 0.759 | 4.886 | 0.773 |
| INTC | low | 19162 | 1.194 | 174696.000 | 2.223 | 1.000 | 2.222 | 1.000 |
| INTC | middle | 19150 | 1.245 | 171740.500 | 2.315 | 1.000 | 2.325 | 1.000 |
| INTC | high | 19150 | 1.280 | 154561.500 | 2.415 | 1.000 | 2.397 | 1.000 |
| MSFT | low | 19794 | 1.235 | 174687.000 | 2.040 | 1.000 | 2.034 | 1.000 |
| MSFT | middle | 19811 | 1.216 | 174314 | 2.016 | 1.000 | 2.008 | 1.000 |
| MSFT | high | 19777 | 1.304 | 170440 | 2.163 | 1.000 | 2.161 | 1.000 |

## Main findings

C++/Python replay parity passed for **1,041,456** side/size observations across **173,576** snapshots. Maximum absolute shortfall difference: **0 bps**. Python in-process sweep time: **1.64 s**; C++ CLI round trip: **8.94 s**. These timings have different measurement boundaries, so they should not be read as a speedup ratio.

| ticker | low_vol_buy_bps | high_vol_buy_bps | high_minus_low_bps |
|---|---|---|---|
| AMZN | 4.060 | 5.212 | 1.151 |
| AAPL | 1.827 | 2.487 | 0.660 |
| GOOG | 3.366 | 5.259 | 1.893 |
| INTC | 2.223 | 2.415 | 0.192 |
| MSFT | 2.040 | 2.163 | 0.122 |

The high-volatility tercile had higher 500-share buy slippage in all five stocks, by 0.122 to 1.893 bps. Average spread also increased from low to high volatility in every stock. This is a consistent within-day association across these names, though spread and other market conditions may explain part of it.

For the liquidity comparison, each taker side is bucketed by the depth it actually consumes. Fill rates increase from thin to deep in every ticker. Slippage falls across the same buckets in some stocks but is not uniformly monotonic; for very deep INTC and MSFT books, a 500-share order is small enough that spread and other variation dominate the depth effect.

## Controlled synthetic stress experiment

The synthetic experiment runs 10,000 trials per condition with a fixed 40-unit buy order. Each book has five levels per side and equal depth at every level. A zero-mean fair-value shock in the range ±0, ±2, or ±10 ticks occurs after quotes are posted; quotes remain fixed. The table reports fill rate and mean absolute difference between execution VWAP and shocked fair value. Keeping quotes fixed isolates displayed depth and stale-quote uncertainty. The shock experiment is synthetic, not an empirical volatility estimate.

| depth_per_level | shock_half_range_ticks | trials | order_size | mean_fill_rate | mean_abs_slippage_ticks |
|---|---|---|---|---|---|
| 2 | 0 | 10000 | 40 | 0.250 | 3.000 |
| 2 | 2 | 10000 | 40 | 0.250 | 3.008 |
| 2 | 10 | 10000 | 40 | 0.250 | 5.708 |
| 10 | 0 | 10000 | 40 | 1.000 | 2.500 |
| 10 | 2 | 10000 | 40 | 1.000 | 2.494 |
| 10 | 10 | 10000 | 40 | 1.000 | 5.515 |
| 50 | 0 | 10000 | 40 | 1.000 | 1.000 |
| 50 | 2 | 10000 | 40 | 1.000 | 1.397 |
| 50 | 10 | 10000 | 40 | 1.000 | 5.226 |

## Interpretation and limits

The comparisons are within ticker: thin versus deep books for depth, and low versus high volatility. The patterns recur across some of the five stocks, but one day of data cannot establish causality or say whether they hold today. Spread and time of day may also differ between buckets. The replay assumes access to the displayed book and does not model order routing or market impact.

The [LiT paper](https://doi.org/10.3389/frai.2025.1616485) is a forecasting paper using Binance data and a transformer plus temporal modeling; it is related background rather than a method this project reproduces.

## Reproduction and artifacts

Run `python -m unittest discover -v`, `python analyze_lobster.py`, and `python experiment.py`. The analysis writes `results/lobster_metrics.json`, `results/lobster_execution_observations.csv`, `results/synthetic_stress_results.csv`, and this report. The raw archives are not committed. Follow `data/README.md` to obtain the samples; the row-level output is generated locally and excluded from Git.

## References

1. Xiao, Y. et al. (2025). *LiT: limit order book transformer*. Frontiers in Artificial Intelligence, 8:1616485. [Article](https://doi.org/10.3389/frai.2025.1616485).
2. LOBSTER. [Sample files](https://php.lobsterdata.com/info/DataSamples.php) and [message/order-book output structure](https://php.lobsterdata.com/info/DataStructure.php).
