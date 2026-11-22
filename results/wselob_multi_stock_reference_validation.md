# WSELOB Five-Stock Reference Validation

Sampled top-ten books were replayed with both MOSAIC's reconstruction and the dataset author's reference implementation.
The check covers 9 dates, the first 5,000 source events per date, and stride 100.
It is a sampled implementation check, not an exhaustive validation of every event or full trading day.

| Stock | Matched books | Mismatches |
|---|---:|---:|
| PEKAO | 425 | 0 |
| KGHM | 377 | 0 |
| PKOBP | 401 | 0 |
| PKNORLEN | 418 | 0 |
| PZU | 401 | 0 |

**Total:** 2,022 matched sampled books; **0 mismatches**.

The machine-readable file records dates, event counts, and per-session totals.

Reproduce with `python validate_wselob_multi_stock.py` after downloading the five files described in [data/README.md](../data/README.md).
