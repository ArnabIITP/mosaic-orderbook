# Explicit-Execution LOBSTER Policy Backtest

## Setup

A fixed top-five depth-imbalance rule is replayed on 5 LOBSTER stocks for one session (2012-06-21). Both policies receive the same non-overlapping signal intents: the next intent is generated only when both are flat, sampled every 1,000 events. The requested size is 100 shares; passive orders queue at the best quote for 250 ms, then any filled quantity is held to 1000 ms and exited against the contemporaneous displayed book. The marketable baseline sweeps at entry and uses the same exit rule. Fees are assumed, not sourced from exchange fee schedules.

Visible type-4 execution messages reduce queue ahead and then fill the passive order; type-2/3 cancellations reduce queue ahead without a fill; type-1 additions after submission are behind; hidden type-5 executions are not treated as visible queue depletion. The initial ahead queue is aggregate displayed size because order IDs preceding the sample are unavailable.

## Pooled results

| Policy | Orders | Any-fill | Entry fill fraction | Completed entries | Incomplete exits | End-session canceled shares | Net realized P&L | Fees | Final inventory | Final marked P&L | Marked bps / turnover | Sum of per-stock max drawdowns | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Passive FIFO | 332 | 0.0% | 0.0% | 0 | 0 | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 | $0 |
| Marketable baseline | 332 | 100.0% | 99.1% | 329 | 2 | 0 | $-7304.57 | $2357.64 | -127 | $-7613.62 | -3.23 | $7728.04 | $23,576,428 |

## Per-stock results

| Ticker | Policy | Orders | Any-fill | Fill fraction | End-session canceled shares | Net realized P&L | Fees | Final inventory | Final marked P&L | Marked bps / turnover | Max drawdown |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AMZN | Passive FIFO | 72 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| AMZN | Marketable baseline | 72 | 100.0% | 99.8% | 0 | $-1368.59 | $319.12 | -70 | $-1352.93 | -4.24 | $1405.43 |
| AAPL | Passive FIFO | 148 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| AAPL | Marketable baseline | 148 | 100.0% | 98.6% | 0 | $-4371.51 | $1703.03 | 0 | $-4371.51 | -2.57 | $4371.51 |
| GOOG | Passive FIFO | 26 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| GOOG | Marketable baseline | 26 | 100.0% | 96.6% | 0 | $-1418.47 | $286.50 | 43 | $-1742.69 | -6.08 | $1804.61 |
| INTC | Passive FIFO | 49 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| INTC | Marketable baseline | 49 | 100.0% | 100.0% | 0 | $-80.30 | $26.30 | -100 | $-80.80 | -3.07 | $80.80 |
| MSFT | Passive FIFO | 37 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| MSFT | Marketable baseline | 37 | 100.0% | 100.0% | 0 | $-65.69 | $22.69 | 0 | $-65.69 | -2.90 | $65.69 |

## Interpretation and limits

This is the first event-driven check with explicit visible executions, partial fills, cancellations, inventory accounting, fees, and a marketable comparator. It does not validate the WSELOB queue estimates: it uses a different venue, five-level sample, one historical day, and a hand-specified imbalance rule. Orders are non-overlapping and paired across policies within each ticker; no statistical significance is claimed from a single day. Dollar P&L is also shown in bps per round-trip turnover to make the different share prices more comparable.

The simulator has no market impact or replenishment model. Post-only orders that would become marketable are canceled conservatively. If exit depth is insufficient, residual inventory remains and is marked at the final midpoint. Fees are illustrative. See `data/README.md` for data terms and source details.

Reproduce with `python lobster_queue_backtest.py`.
