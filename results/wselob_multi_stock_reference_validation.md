# WSELOB Five-Stock Reference Validation

Sampled top-ten books were replayed with both MOSAIC's reconstruction and the dataset author's reference implementation.
The check covers 9 dates, the first 100,000 source events per date, and stride 100.
It is a sampled implementation check, not an exhaustive validation of every event or full trading day.

| Stock | Matched books | Mismatches |
|---|---:|---:|
| PEKAO | 2,986 | 0 |
| KGHM | 6,933 | 0 |
| PKOBP | 5,595 | 0 |
| PKNORLEN | 6,002 | 0 |
| PZU | 6,066 | 0 |

**Total:** 27,582 matched sampled books; **0 mismatches**.

The machine-readable file records dates, event counts, and per-session totals.

Reproduce with `python validate_wselob_multi_stock.py --events-per-day 100000` after downloading the five files described in [data/README.md](../data/README.md).
