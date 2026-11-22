# Explicit-Execution LOBSTER Policy Backtest

## Setup

A fixed top-five depth-imbalance rule is replayed on 5 LOBSTER stocks for one session (2012-06-21). Both policies receive the same non-overlapping signal intents: the next intent is generated only when both are flat, sampled every 1,000 events. The requested size is 100 shares; passive orders queue at the best quote for 2000 ms, then any filled quantity is held to 5000 ms and exited against the contemporaneous displayed book. The marketable baseline sweeps at entry and uses the same exit rule. Fees are assumed, not sourced from exchange fee schedules.

Visible type-4 execution messages reduce queue ahead and then fill the passive order; type-2/3 cancellations reduce queue ahead without a fill; type-1 additions after submission are behind; hidden type-5 executions are not treated as visible queue depletion. The initial ahead queue is aggregate displayed size because order IDs preceding the sample are unavailable.

## Pooled results

| Policy | Orders | Any-fill | Entry fill fraction | Completed entries | Incomplete exits | End-session canceled shares | Net realized P&L | Fees | Final inventory | Final marked P&L | Marked bps / turnover | Sum of per-stock max drawdowns | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Passive FIFO | 382 | 0.0% | 0.0% | 0 | 0 | 100 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 | $0 |
| Marketable baseline | 382 | 100.0% | 98.7% | 381 | 0 | 0 | $-9372.22 | $2881.61 | -100 | $-9372.72 | -3.25 | $9396.56 | $28,816,129 |

## Per-stock results

| Ticker | Policy | Orders | Any-fill | Fill fraction | End-session canceled shares | Net realized P&L | Fees | Final inventory | Final marked P&L | Marked bps / turnover | Max drawdown |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AMZN | Passive FIFO | 85 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| AMZN | Marketable baseline | 85 | 100.0% | 99.9% | 0 | $-1608.48 | $378.11 | 0 | $-1608.48 | -4.25 | $1608.48 |
| AAPL | Passive FIFO | 148 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| AAPL | Marketable baseline | 148 | 100.0% | 98.6% | 0 | $-4565.11 | $1703.04 | 0 | $-4565.11 | -2.68 | $4565.11 |
| GOOG | Passive FIFO | 69 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| GOOG | Marketable baseline | 69 | 100.0% | 95.8% | 0 | $-3079.96 | $754.75 | 0 | $-3079.96 | -4.08 | $3101.81 |
| INTC | Passive FIFO | 43 | 0.0% | 0.0% | 100 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| INTC | Marketable baseline | 43 | 100.0% | 100.0% | 0 | $-61.03 | $23.03 | -100 | $-61.53 | -2.67 | $62.75 |
| MSFT | Passive FIFO | 37 | 0.0% | 0.0% | 0 | $0.00 | $0.00 | 0 | $0.00 | n/a | $0.00 |
| MSFT | Marketable baseline | 37 | 100.0% | 100.0% | 0 | $-57.65 | $22.69 | 0 | $-57.65 | -2.54 | $58.42 |

## Interpretation and limits

This is the first event-driven check with explicit visible executions, partial fills, cancellations, inventory accounting, fees, and a marketable comparator. It does not validate the WSELOB queue estimates: it uses a different venue, five-level sample, one historical day, and a hand-specified imbalance rule. Orders are non-overlapping and paired across policies within each ticker; no statistical significance is claimed from a single day. Dollar P&L is also shown in bps per round-trip turnover to make the different share prices more comparable.

The simulator has no market impact or replenishment model. Post-only orders that would become marketable are canceled conservatively. If exit depth is insufficient, residual inventory remains and is marked at the final midpoint. Fees are illustrative. See `data/README.md` for data terms and source details.

Reproduce with `python lobster_queue_backtest.py`.
