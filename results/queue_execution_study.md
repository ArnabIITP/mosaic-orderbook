# Queue-Aware Passive Execution Sensitivity

## Protocol

The study samples 383 directional boosted-tree calls from held-out PEKAO dates, using every 20th call per session. Up calls place a passive buy at the best bid; down calls place a passive sell at the best ask. Each shadow order queues behind visible same-price orders through the target quote. Arrival delay is 0 ms and is rounded to the first complete source timestamp group.

The passive entry pays an assumed 0.1 bp maker fee and exits by sweeping displayed opposite-side depth at the target quote, paying 1 bp. The comparator immediately sweeps displayed depth at entry and again at the target, paying 1 bp per side. P&L is reported per completed notional and per requested initial notional; unfilled entry quantity contributes zero to the latter.

## Policy comparison

| Policy / M-message assumption | Orders | Entry fill probability | Mean entry fill fraction | Completed round trips | Net bps / completed notional | Net bps / requested notional | Date-bootstrap 95% interval |
|---|---:|---:|---:|---:|---:|---:|---:|
| Passive (M decreases as execution proxy) | 383 | 23.0% | 11.2% | 23.0% | -4.28 | -0.60 | [-0.97, -0.26] |
| Passive (M decreases as cancellations) | 383 | 0.0% | 0.0% | 0.0% | n/a | 0.00 | [0.00, 0.00] |
| Marketable entry + exit | 383 | 100.0% | 100.0% | 100.0% | -17.14 | -17.14 | [-19.20, -16.18] |

## Limits

This is a displayed-book policy comparison, not a portfolio backtest. WSELOB M messages can mean execution or cancellation and do not identify trade prints; the two passive scenarios bracket that ambiguity. The marketable comparator and passive exit assume static displayed depth, with no impact, replenishment, hidden liquidity, own-order interference, or risk limits. Orders overlap and are not independent strategy trades. Results cover one stock in 2017 and the selected sampling schedule.

Reproduce after running the WSELOB analysis and execution-aware benchmark:

```powershell
python execution_aware_benchmark.py --fee-bps-per-side 1
python queue_execution_study.py
python queue_execution_study.py --order-size 100 --output-json results/queue_execution_100share_metrics.json --output-report results/queue_execution_100share_study.md
python queue_execution_study.py --latency-ms 100 --output-json results/queue_execution_latency100ms_metrics.json --output-report results/queue_execution_latency100ms_study.md
```
