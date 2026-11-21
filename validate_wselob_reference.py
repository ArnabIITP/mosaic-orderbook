"""Compare our reconstructed books with the WSELOB author's reference code."""

from __future__ import annotations

import argparse
import importlib.util
from collections import Counter
import json
from pathlib import Path
from typing import Any

import h5py

from analyze_wselob import DEFAULT_DATA, _reconstruct_day

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "data" / "wselob_2017" / "orderbook2.py"


def _load_reference() -> Any:
    spec = importlib.util.spec_from_file_location("wselob_author_reference", REFERENCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load reference implementation at {REFERENCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OrderBook


def _top_levels(book: Any, side: int) -> list[tuple[int, int]]:
    source = book.buy_orders if side == 1 else book.sell_orders
    totals: Counter[int] = Counter()
    for price, volume, order_type in zip(source["price"], source["volume"], source["order_type"]):
        p, q, kind = int(price), int(volume), str(order_type)
        if p > 0 and q > 0 and kind in ("2", "P", "K"):
            totals[p] += q
    return sorted(totals.items(), reverse=(side == 1))[:10]


def validate(data_path: Path = DEFAULT_DATA, days: int = 5, events_per_day: int = 5000, stride: int = 100,
             selected_dates: list[str] | None = None) -> dict[str, Any]:
    """Compare complete ten-level snapshots at transaction timestamp boundaries."""
    if min(days, events_per_day, stride) < 1:
        raise ValueError("days, events_per_day, and stride must be positive")
    ReferenceBook = _load_reference()
    per_date = []
    checked = 0
    with h5py.File(data_path, "r") as data:
        available = sorted(data.keys())
        names = ["d" + d for d in selected_dates] if selected_dates else available[:days]
        missing = [name for name in names if name not in data]
        if missing:
            raise ValueError(f"date group(s) not present in HDF5: {missing}")
        for name in names:
            raw = data[name]["table"][:events_per_day]
            diagnostics: Counter[str] = Counter()
            own_rows = _reconstruct_day(raw, stride, name.removeprefix("d"), diagnostics)
            by_event_index = {int(row["event_index"]): row for row in own_rows}
            reference = ReferenceBook(verbose=False)
            day_checks = 0

            def compare(index: int) -> None:
                nonlocal checked, day_checks
                ours = by_event_index.get(index)
                if ours is None:
                    return
                bids, asks = _top_levels(reference, 1), _top_levels(reference, 2)
                if bids != ours["bids"] or asks != ours["asks"]:
                    raise AssertionError(
                        f"reference mismatch {name} event={index}: "
                        f"bids ours={ours['bids'][:3]} ref={bids[:3]}, "
                        f"asks ours={ours['asks'][:3]} ref={asks[:3]}"
                    )
                checked += 1
                day_checks += 1

            group_indices: list[int] = []
            previous_time: int | None = None
            for i, row in enumerate(raw):
                timestamp = int(row["time"])
                if previous_time is not None and timestamp != previous_time:
                    if any(k % stride == 0 for k in group_indices):
                        compare(i - 1)
                    group_indices.clear()
                values: dict[str, Any] = {}
                for field in raw.dtype.names or ():
                    value = row[field]
                    if field in ("action_type", "order_type"):
                        values[field] = bytes(value).decode().strip()
                    else:
                        values[field] = int(value)
                action = values["action_type"]
                if action == "F":
                    reference.clear_orderbook()
                elif action == "A":
                    reference.add_order(values)
                elif action == "M":
                    reference.mod_order(values)
                elif action == "D":
                    reference.del_order(values)
                elif action == "Y":
                    reference.retransmit_order(values)
                else:
                    raise ValueError(f"unexpected WSELOB action {action!r}")
                group_indices.append(i)
                previous_time = timestamp
            if group_indices and any(k % stride == 0 for k in group_indices):
                compare(len(raw) - 1)
            per_date.append({"date": name.removeprefix("d"), "events_replayed": len(raw), "matching_snapshots": day_checks})
    return {"reference": str(REFERENCE.relative_to(ROOT)), "dates": len(per_date), "events_per_date": events_per_day, "stride": stride, "matched_snapshots": checked, "mismatches": 0, "per_date": per_date}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--events-per-day", type=int, default=5000)
    parser.add_argument("--stride", type=int, default=100)
    parser.add_argument("--dates", nargs="*", help="specific YYYYMMDD groups to validate instead of the first N dates")
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "wselob_2017_reference_validation.json")
    args = parser.parse_args()
    result = validate(args.data, args.days, args.events_per_day, args.stride, args.dates)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
