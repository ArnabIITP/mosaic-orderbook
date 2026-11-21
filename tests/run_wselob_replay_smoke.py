"""Create a tiny feed fixture and exercise the complete C++ replay pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_wselob_cpp import replay


DTYPE = [
    ("index", "i8"), ("time", "i8"), ("priority_date", "i8"),
    ("order_date", "i8"), ("symbol_idx", "i8"), ("price", "i8"),
    ("agg_volume", "i8"), ("volume", "i8"), ("order_id", "i8"),
    ("num_orders", "i8"), ("side", "i1"), ("order_type", "S2"),
    ("action_type", "S1"), ("price_level", "i1"),
]


def _day(date: int, base_time: int) -> np.ndarray:
    messages = [
        (-1, 0, 0, 0, 0, -1, b"-1", b"F"),
        (10, 9900, 10, 10, 1, 1, b"2", b"A"),
        (10, 10100, 8, 8, 2, 2, b"2", b"A"),
        (10, 9900, 15, 5, 3, 1, b"2", b"A"),
        (10, 9900, 11, 6, 1, 1, b"2", b"M"),
        (10, 10200, 12, 12, 4, 2, b"2", b"Y"),
        (10, 9900, 6, 5, 3, 1, b"2", b"D"),
        (10, 10150, 4, 4, 5, 2, b"K", b"A"),
        (10, 0, 0, 2, 6, 1, b"1", b"A"),
        (10, 0, 0, 2, 6, 1, b"1", b"D"),
    ]
    rows = np.zeros(len(messages), dtype=DTYPE)
    rows["index"] = np.arange(len(messages))
    rows["time"] = base_time + np.arange(len(messages), dtype=np.int64) * 1_000_000_000
    rows["priority_date"] = -1
    rows["order_date"] = [item[0] for item in messages]
    rows["order_date"][1:] = date * 1_000_000_000
    rows["price"] = [item[1] for item in messages]
    rows["agg_volume"] = [item[2] for item in messages]
    rows["volume"] = [item[3] for item in messages]
    rows["order_id"] = [item[4] for item in messages]
    rows["side"] = [item[5] for item in messages]
    rows["order_type"] = [item[6] for item in messages]
    rows["action_type"] = [item[7] for item in messages]
    rows["price_level"] = 2
    return rows


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        data_path = root / "smoke.h5"
        with h5py.File(data_path, "w") as data:
            data.create_group("d20200102").create_dataset("table", data=_day(20200102, 1_500_000_000_000_000_000))
            data.create_group("d20200103").create_dataset("table", data=_day(20200103, 1_500_100_000_000_000_000))
        result = replay(data_path, stride=2, repetitions=2, output_json=root / "result.json")
        if result["mismatches"] != 0 or result["matched_snapshots"] == 0:
            raise AssertionError(f"replay smoke failed: {result}")
        print(f"C++ feed replay smoke passed: {result['raw_events']} events, {result['matched_snapshots']} snapshots")


if __name__ == "__main__":
    main()
