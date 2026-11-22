# Queue Execution and Policy Sensitivity

Each scenario replays the same 383 held-out directional PEKAO calls. Passive orders join the displayed best quote behind visible FIFO size; the marketable baseline sweeps displayed depth at entry and at the target. Bps are normalized to requested initial notional, with unfilled passive quantity contributing zero.

| Scenario | Policy / M-decrease treatment | Fill probability | Mean fill fraction | Mean net bps per requested notional | Date-bootstrap 95% interval |
|---|---|---:|---:|---:|---:|
| 500 shares, immediate | Passive; M decreases as execution proxy | 23.0% | 11.2% | -0.60 | [-0.97, -0.26] |
| 500 shares, immediate | Passive; M decreases as cancellation | 0.0% | 0.0% | 0.00 | [0.00, 0.00] |
| 500 shares, immediate | Marketable entry and exit | 100.0% | 100.0% | -17.14 | [-19.20, -16.18] |
| 100 shares, immediate | Passive; M decreases as execution proxy | 23.0% | 19.2% | -0.78 | [-1.24, -0.37] |
| 100 shares, immediate | Passive; M decreases as cancellation | 0.0% | 0.0% | 0.00 | [0.00, 0.00] |
| 100 shares, immediate | Marketable entry and exit | 100.0% | 100.0% | -12.96 | [-14.84, -12.08] |
| 500 shares, 100 ms arrival | Passive; M decreases as execution proxy | 21.9% | 10.4% | -0.56 | [-0.93, -0.24] |
| 500 shares, 100 ms arrival | Passive; M decreases as cancellation | 0.0% | 0.0% | 0.00 | [0.00, 0.00] |
| 500 shares, 100 ms arrival | Marketable entry and exit | 100.0% | 100.0% | -17.14 | [-19.20, -16.18] |

## Read this result carefully

Passive fill estimates depend on an ambiguous source message. WSELOB `M` updates can encode modifications or partial fills, but there is no independent trade-print stream. Treating every same-price reduction as execution gives a sensitivity scenario; treating reductions as cancellations gives a no-fill conservative case. The apparent positive midpoint markout among fills does not survive as positive P&L after a displayed-book exit and taker fee.

The marketable comparison is not a production simulator: both policies use static reconstructed depth, assume immediate access at sampled event times, and omit market impact, replenishment, hidden liquidity, order interaction, inventory constraints, and risk controls. Signal horizons overlap. The bootstrap resamples whole dates, but this selected-call study is still diagnostic rather than a portfolio backtest.

See [the protocol and detailed assumptions](queue_execution_study.md). Reproduce the runs using its commands.
