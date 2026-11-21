# WSELOB 2017 PEKAO Execution Study

## Executive summary

The replay rebuilds PEKAO's displayed limit book for 250 trading dates in 2017 from 9,869,579 order updates. The execution study uses 94,390 snapshots sampled every 100 source events. Each C++ sweep is checked against the Python implementation. These results cover one stock and historical displayed depth; they do not establish causality or predict current execution.

## Research question and related work

The study measures how displayed liquidity and recent price variability relate to the cost and fill rate of hypothetical marketable orders. The supplied [LiT paper](https://doi.org/10.3389/frai.2025.1616485) predicts price movements from order-book sequences; this study instead measures execution cost and does not reproduce LiT.

## Data and reconstruction

- **Source:** PEKAO file from [WSELOB-2017 on Mendeley Data](https://data.mendeley.com/datasets/3g4mhdp899/1), DOI [10.17632/3g4mhdp899.1](https://doi.org/10.17632/3g4mhdp899.1). The dataset record describes Warsaw Stock Exchange order-book data for 2017 and is licensed CC BY 4.0. The downloaded HDF5 is 152,759,953 bytes; SHA-256: `3c418a55a492ebe2e39c8513cd7fc7e3e6827dc1a176af09fdcaad9f3485bae6`.
- **Coverage:** 20170102 through 20171229; 250 dates, 9,869,579 raw events, 94,390 usable snapshots. The source has five equities, but this run uses PEKAO only.
- **State reconstruction:** orders are keyed by `(order_date, order_id)`. Adds and retransmissions create or update displayed orders; modifications preserve omitted fields and replace remaining quantity; deletes remove orders; full-refresh messages clear the book. Snapshots are taken at timestamp-group boundaries. The source `agg_volume` field was checked at those boundaries: 9,796,236 checks, with 1,617 differences. Example rows and the remaining reconstruction counters are in `wselob_2017_metrics.json`.
- **Reference validation:** the author's `orderbook2.py` implementation matched all 425 sampled ten-level books across 9 dates with zero mismatches (first 5,000 events per date).
- **Scaling:** prices are source integer units (100 units per PLN); quantities are shares. Basis-point ratios are invariant to the price scaling.

## Method

The book is sampled when each 100-event threshold is reached, then captured after all updates with that timestamp have been applied. Crossed or one-sided states are omitted. We compute trailing one-minute realised volatility from sampled midpoints independently within each trading date and omit the first minute. Liquidity buckets use that date's terciles of executable-side top-ten depth; volatility buckets use that date's volatility terciles. The 500-share comparisons are descriptive and do not control for spread or time of day.

For each snapshot, hypothetical marketable buys consume asks and sells consume bids at displayed prices, up to ten occupied levels. Shortfall is VWAP relative to the snapshot midpoint; fill rate is filled/requested quantity. This includes the static book sweep's depth-dependent price concession, not endogenous market impact after the order changes the book.

Latency sensitivity executes using the first sampled book at least 1 or 5 seconds after the decision timestamp, while measuring price relative to the decision midpoint. The report varies fees from 0 to 5 bps per side. Because observations are sampled every 100 events, latency timing is quantized by the next sampled observation and is not an exchange/network latency simulation. Fees are explicit scenario assumptions, not observed broker fees.

The C++17 matcher is used for the reported execution outcomes and Python independently replays each side/size. Baseline replay passed 566,340 comparisons over 94,390 snapshots; delayed replay passed 1,129,662 additional comparisons. Maximum shortfall difference was 0 bps. Python sweep elapsed 0.75s; baseline C++ CLI elapsed 7.13s. These timings have different measurement boundaries and should not be treated as a speedup ratio.

## C++ full-stream replay benchmark

A separate Release build (`g++ 14.2.0 (-O3)`, `-O3 -DNDEBUG`) replayed all 9,869,579 source updates 3 times. Median wall time was 6.56s (1,504,284 updates/s), with run times 4.85s, 6.56s, 7.51s. It emitted 96,465 sampled books and matched Python exactly (zero mismatches); output SHA-256 `599830ee8be735abf9ddf210f3bf87546796463f3e6cf4f8492c1bc21c277cde`. This includes binary feed I/O, parsing, state updates, and snapshot output. Python's 168.97s reconstruction reads/decompresses HDF5 and is not a controlled language-versus-language benchmark.

The independent order-book reference comparison matched 425 snapshots over 9 dates with zero mismatches. The `agg_volume` audit found 1,617 differences across 9,796,236 checks (0.0165%); example rows and the full counters are in the metrics file.

## Overall execution outcomes

| side | order_shares | snapshots | mean_slippage_bps_filled | mean_fill_rate |
|---|---|---|---|---|
| buy | 100 | 94390 | 5.352 | 1.000 |
| sell | 100 | 94390 | 5.483 | 1.000 |
| buy | 500 | 94390 | 7.085 | 1.000 |
| sell | 500 | 94390 | 7.298 | 1.000 |
| buy | 1000 | 94390 | 9.014 | 1.000 |
| sell | 1000 | 94390 | 9.239 | 1.000 |

## Main findings

For the 500-share order, mean slippage rises from 7.08 bps for buys and 7.30 bps for sells to 9.01 and 9.24 bps at 1,000 shares. The larger sweep consumes more price levels, which is the measured static depth cost.

Across dates, high-volatility versus low-volatility 500-share slippage is higher by 3.53 bps for buys (date-bootstrap 95% interval 3.36 to 3.69) and 3.67 bps for sells (3.49 to 3.84). High-volatility buckets also have a wider median spread (0.10 to 0.15 PLN). This points to spread widening as part of the association; the analysis does not isolate a causal volatility effect.

Thin versus deep displayed-depth differences are not reliably ordered: the paired daily intervals span zero for buys (-0.35 to 0.09 bps) and sells (-0.16 to 0.24 bps). For these order sizes, a 500-share sweep is small relative to the median top-ten depth, so spread and other conditions dominate this coarse depth split.

## 500-share volatility terciles

| side | bucket | observations | median_spread_pln | median_consumable_depth_shares | mean_500_share_slippage_bps | mean_fill_rate |
|---|---|---|---|---|---|---|
| buy | low | 31603 | 0.100 | 10037 | 5.518 | 1.000 |
| buy | middle | 31364 | 0.100 | 10034.000 | 6.623 | 1.000 |
| buy | high | 31423 | 0.150 | 10011 | 9.122 | 1.000 |
| sell | low | 31603 | 0.100 | 9954 | 5.673 | 1.000 |
| sell | middle | 31364 | 0.100 | 10054.000 | 6.791 | 1.000 |
| sell | high | 31423 | 0.150 | 10013 | 9.437 | 1.000 |

## 500-share executable-depth terciles

| side | bucket | observations | median_spread_pln | median_consumable_depth_shares | mean_500_share_slippage_bps | mean_fill_rate |
|---|---|---|---|---|---|---|
| buy | thin | 31554 | 0.100 | 6761.000 | 7.122 | 1.000 |
| buy | middle | 31383 | 0.100 | 10081 | 6.890 | 1.000 |
| buy | deep | 31453 | 0.140 | 13885 | 7.241 | 1.000 |
| sell | thin | 31558 | 0.100 | 6720.500 | 7.367 | 1.000 |
| sell | middle | 31385 | 0.100 | 9973 | 7.128 | 1.000 |
| sell | deep | 31447 | 0.140 | 13851 | 7.396 | 1.000 |

## Paired daily bucket differences

Differences are computed within each date, then averaged with dates weighted equally. The 95% intervals come from a seeded bootstrap that resamples dates rather than individual snapshots. Volatility is high minus low; depth is thin minus deep. These comparisons do not adjust for spread or time of day.

### Volatility: high minus low

| side | comparison | dates | mean_daily_difference_bps | ci95_low_bps | ci95_high_bps |
|---|---|---|---|---|---|
| buy | high minus low | 250 | 3.528 | 3.358 | 3.692 |
| sell | high minus low | 250 | 3.665 | 3.494 | 3.844 |

### Depth: thin minus deep

| side | comparison | dates | mean_daily_difference_bps | ci95_low_bps | ci95_high_bps |
|---|---|---|---|---|---|
| buy | thin minus deep | 250 | -0.130 | -0.350 | 0.091 |
| sell | thin minus deep | 250 | 0.045 | -0.159 | 0.244 |

## Latency and fee sensitivity

The grid varies assumed per-side fees over 0, 1, 5 bps at 1- and 5-second book delays. These costs are measured from the decision midpoint and reported by order side.

| latency_seconds | fee_bps_per_side | side | snapshots | mean_net_slippage_bps_500_shares | mean_abs_midpoint_move_bps |
|---|---|---|---|---|---|
| 1 | 0.000 | buy | 94140 | 6.987 | 4.561 |
| 1 | 1.000 | buy | 94140 | 7.987 | 4.561 |
| 1 | 5.000 | buy | 94140 | 11.987 | 4.561 |
| 1 | 0.000 | sell | 94140 | 7.254 | 4.561 |
| 1 | 1.000 | sell | 94140 | 8.254 | 4.561 |
| 1 | 5.000 | sell | 94140 | 12.254 | 4.561 |
| 5 | 0.000 | buy | 94137 | 6.859 | 4.758 |
| 5 | 1.000 | buy | 94137 | 7.859 | 4.758 |
| 5 | 5.000 | buy | 94137 | 11.859 | 4.758 |
| 5 | 0.000 | sell | 94137 | 7.193 | 4.758 |
| 5 | 1.000 | sell | 94137 | 8.193 | 4.758 |
| 5 | 5.000 | sell | 94137 | 12.193 | 4.758 |

## Interpretation and limitations

These are conditional associations in PEKAO's 2017 data. High-volatility periods can also have wider spreads, different depth, and different times of day. The snapshots omit hidden liquidity, queue position, later cancellations, replenishment, and order-arrival competition. The latency proxy uses a later sampled book and cannot show the intervening path. Fees are illustrative; the results are not expected live fills or returns.

## Reproduction

Install Python 3.10+, `h5py`, and a C++17 compiler. From the project root run:

```powershell
python -m pip install -r requirements-research.txt
python -m pip install -r requirements-validation.txt
python validate_wselob_reference.py --days 5 --events-per-day 5000
python replay_wselob_cpp.py --stride 100 --repetitions 3
python analyze_wselob.py --stride 100 --fee-bps 1
```

Outputs: `wselob_2017_execution_observations.csv`, `wselob_2017_latency_observations.csv`, `wselob_2017_metrics.json`, and this report.
