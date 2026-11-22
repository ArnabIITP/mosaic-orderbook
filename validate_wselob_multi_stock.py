"""Validate sampled books for all five WSELOB instruments against the author's reference."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from validate_wselob_reference import validate

ROOT = Path(__file__).resolve().parent
TICKERS = ("KGHM", "PEKAO", "PKOBP", "PKNORLEN", "PZU")
DATES = ("20170102", "20170215", "20170330", "20170518", "20170703",
         "20170816", "20170928", "20171113", "20171229")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "wselob_2017")
    parser.add_argument("--events-per-day", type=int, default=5000)
    parser.add_argument("--stride", type=int, default=100)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "wselob_multi_stock_reference_validation.json")
    args = parser.parse_args()
    paths = {"PEKAO": args.data_dir / "PEKAO_lob_2017_zlib.h5"}
    paths.update({ticker: args.data_dir / "all_stocks" / f"{ticker}_lob_2017_zlib.h5"
                  for ticker in TICKERS if ticker != "PEKAO"})
    missing = [f"{ticker}: {path}" for ticker, path in paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing WSELOB files:\n" + "\n".join(missing))

    results: dict[str, Any] = {}
    for ticker, path in paths.items():
        print(f"Validating {ticker}...", flush=True)
        results[ticker] = validate(path, events_per_day=args.events_per_day,
                                   stride=args.stride, selected_dates=list(DATES))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    total = sum(item["matched_snapshots"] for item in results.values())
    failures = sum(item["mismatches"] for item in results.values())
    rows = [
        "# WSELOB Five-Stock Reference Validation", "",
        "Sampled top-ten books were replayed with both MOSAIC's reconstruction and the dataset author's reference implementation.",
        f"The check covers {len(DATES)} dates, the first {args.events_per_day:,} source events per date, and stride {args.stride:,}.",
        "It is a sampled implementation check, not an exhaustive validation of every event or full trading day.", "",
        "| Stock | Matched books | Mismatches |", "|---|---:|---:|",
    ]
    for ticker, item in results.items():
        rows.append(f"| {ticker} | {item['matched_snapshots']:,} | {item['mismatches']:,} |")
    rows.extend(["", f"**Total:** {total:,} matched sampled books; **{failures} mismatches**.", "",
                 "The machine-readable file records dates, event counts, and per-session totals.", "",
                 "Reproduce with `python validate_wselob_multi_stock.py` after downloading the five files described in [data/README.md](../data/README.md).", ""])
    (ROOT / "results" / "wselob_multi_stock_reference_validation.md").write_text("\n".join(rows), encoding="utf-8")
    print(f"Validated {total:,} sampled books across five stocks; mismatches={failures}.")


if __name__ == "__main__":
    main()
