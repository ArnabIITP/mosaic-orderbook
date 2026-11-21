"""Replay raw WSELOB updates in C++ and compare sampled books with Python."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import statistics
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import h5py

from analyze_wselob import DEFAULT_DATA, ROOT, _reconstruct_day

EVENT = struct.Struct("<IIqcqqbqqc")


def _python_replay(data_path: Path, stride: int, days: int | None, events_per_day: int | None) -> tuple[list[str], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    date_names: list[str] = []
    by_date: dict[str, list[dict[str, Any]]] = {}
    aggregate: dict[str, Any] = {}
    with h5py.File(data_path, "r") as data:
        groups = sorted(data.keys())
        if days is not None:
            groups = groups[:days]
        for group_name in groups:
            date = group_name.removeprefix("d")
            ds = data[group_name]["table"]
            limit = min(len(ds), events_per_day) if events_per_day is not None else len(ds)
            array = ds[:limit]
            diagnostics: Counter[str] = Counter()
            rows = _reconstruct_day(array, stride, date, diagnostics)
            date_names.append(date)
            by_date[date] = rows
            aggregate[date] = {"events": limit, "snapshots": len(rows), "diagnostics": diagnostics}
    return date_names, by_date, aggregate


def _write_feed(data_path: Path, output_path: Path, dates: list[str], events_per_day: int | None) -> int:
    date_index = {date: i for i, date in enumerate(dates)}
    processed = 0
    with h5py.File(data_path, "r") as data, output_path.open("wb") as output:
        for date in dates:
            ds = data["d" + date]["table"]
            limit = min(len(ds), events_per_day) if events_per_day is not None else len(ds)
            for start in range(0, limit, 50_000):
                for offset, row in enumerate(ds[start:min(start + 50_000, limit)]):
                    action = bytes(row["action_type"])
                    order_type = bytes(row["order_type"]).strip()
                    output.write(EVENT.pack(
                        date_index[date], start + offset, int(row["time"]),
                        action[:1], int(row["order_date"]), int(row["order_id"]),
                        int(row["side"]), int(row["price"]), int(row["volume"]),
                        order_type[:1] if len(order_type) == 1 else b"-",
                    ))
                    processed += 1
    return processed


def _build_binary(binary: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(ROOT / "compare_cpp.py"), "--events", "0", "--binary", str(binary), "--optimization=-O3"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("could not build C++ replay engine:\n" + result.stderr)
    compiler_line = next((line for line in result.stdout.splitlines() if line.startswith("C++ compiler:")), "C++ compiler: unknown")
    return compiler_line.removeprefix("C++ compiler:").strip()


def _check_output(path: Path, dates: list[str], python_rows: dict[str, list[dict[str, Any]]], expected_events: int) -> dict[str, Any]:
    by_event = {date: {r["event_index"]: r for r in rows} for date, rows in python_rows.items()}
    snapshot_count = 0
    digest = hashlib.sha256()
    missing_updates = None
    processed_events = None
    with path.open("rb") as handle:
        for raw_line in handle:
            digest.update(raw_line)
            values = raw_line.decode("ascii").rstrip("\n").split("\t")
            if values[0] == "Q":
                if len(values) != 5 or values[1] != "processed_events" or values[3] != "missing_modify_updates":
                    raise ValueError(f"malformed C++ diagnostics: {raw_line[:200]!r}")
                processed_events, missing_updates = int(values[2]), int(values[4])
                continue
            if values[0] != "S" or len(values) < 6:
                raise ValueError(f"malformed C++ replay output: {raw_line[:200]!r}")
            date_index, event_index = int(values[1]), int(values[2])
            if date_index >= len(dates):
                raise AssertionError(f"C++ returned unknown date index {date_index}")
            date = dates[date_index]
            expected = by_event[date].get(event_index)
            if expected is None:
                raise AssertionError(f"C++ emitted unexpected sample {date} event {event_index}")
            cursor = 4
            ask_count = int(values[cursor]); cursor += 1
            asks = [(int(values[cursor + 2*i]), int(values[cursor + 2*i + 1])) for i in range(ask_count)]
            cursor += ask_count * 2
            bid_count = int(values[cursor]); cursor += 1
            bids = [(int(values[cursor + 2*i]), int(values[cursor + 2*i + 1])) for i in range(bid_count)]
            cursor += bid_count * 2
            if cursor != len(values):
                raise ValueError(f"trailing fields in C++ snapshot {date} event {event_index}")
            if asks != expected["asks"] or bids != expected["bids"]:
                raise AssertionError(
                    f"C++/Python book mismatch {date} event {event_index}: "
                    f"asks C++={asks[:3]} Python={expected['asks'][:3]}, "
                    f"bids C++={bids[:3]} Python={expected['bids'][:3]}"
                )
            snapshot_count += 1
    if processed_events != expected_events:
        raise AssertionError(f"C++ processed {processed_events} events, expected {expected_events}")
    return {"matched_snapshots": snapshot_count, "mismatches": 0, "processed_events": processed_events,
            "missing_modify_updates": missing_updates, "output_sha256": digest.hexdigest()}


def replay(data_path: Path = DEFAULT_DATA, stride: int = 100, repetitions: int = 3,
           days: int | None = None, events_per_day: int | None = None,
           keep_intermediates: bool = False, output_json: Path | None = None) -> dict[str, Any]:
    if stride < 1 or repetitions < 1:
        raise ValueError("stride and repetitions must be positive")
    binary = ROOT / "build" / ("order_book_cli.exe" if sys.platform == "win32" else "order_book_cli")
    feed_file = ROOT / "build" / "wselob_event_stream.bin"
    output_file = ROOT / "build" / "wselob_cpp_snapshots.tsv"

    python_started = time.perf_counter()
    dates, python_rows, python_metadata = _python_replay(data_path, stride, days, events_per_day)
    python_seconds = time.perf_counter() - python_started
    write_started = time.perf_counter()
    event_count = _write_feed(data_path, feed_file, dates, events_per_day)
    feed_write_seconds = time.perf_counter() - write_started
    compiler = _build_binary(binary)

    timings = []
    output_digest = None
    checks: dict[str, Any] | None = None
    for _ in range(repetitions):
        started = time.perf_counter()
        with output_file.open("wb") as stdout:
            result = subprocess.run([str(binary), "--replay-events", str(feed_file), str(stride)],
                                    cwd=ROOT, stdout=stdout, stderr=subprocess.PIPE)
        elapsed = time.perf_counter() - started
        if result.returncode:
            raise RuntimeError("C++ raw event replay failed:\n" + result.stderr.decode(errors="replace"))
        run_checks = _check_output(output_file, dates, python_rows, event_count)
        if output_digest is not None and output_digest != run_checks["output_sha256"]:
            raise AssertionError("C++ replay output changed between benchmark repetitions")
        output_digest = run_checks["output_sha256"]
        checks = run_checks
        timings.append(elapsed)

    missing_python = sum(
        int(v["diagnostics"].get("modify_without_live_resting_order", 0)) +
        int(v["diagnostics"].get("modify_without_live_nonresting_order", 0))
        for v in python_metadata.values()
    )
    if checks and checks["missing_modify_updates"] != missing_python:
        raise AssertionError(f"missing modification diagnostics differ: C++={checks['missing_modify_updates']} Python={missing_python}")

    diagnostic_totals: dict[str, int] = {}
    for day in python_metadata.values():
        for key, value in day["diagnostics"].items():
            if isinstance(value, int):
                diagnostic_totals[key] = diagnostic_totals.get(key, 0) + value
    result = {
        "data": str(data_path.relative_to(ROOT) if data_path.is_relative_to(ROOT) else data_path),
        "dates": len(dates), "date_start": dates[0], "date_end": dates[-1],
        "raw_events": event_count, "stride_raw_events": stride,
        "python_hdf5_reconstruction_seconds": python_seconds,
        "normalized_binary_write_seconds": feed_write_seconds,
        "cpp_runs_seconds": timings,
        "cpp_median_seconds": statistics.median(timings),
        "cpp_min_seconds": min(timings),
        "cpp_build_optimization": "-O3 -DNDEBUG",
        "cpp_compiler": compiler,
        "cpp_events_per_second_median": event_count / statistics.median(timings),
        "cpp_output_bytes": output_file.stat().st_size,
        **(checks or {}),
        "python_missing_modify_updates": missing_python,
        "aggregate_volume_checks": sum(int(v["diagnostics"].get("aggregate_volume_checks", 0)) for v in python_metadata.values()),
        "aggregate_volume_mismatches": sum(int(v["diagnostics"].get("aggregate_volume_mismatches", 0)) for v in python_metadata.values()),
        "python_reconstruction_diagnostic_totals": diagnostic_totals,
        "timing_note": "C++ measurement includes process startup, binary feed I/O, parsing, book mutations, and TSV snapshot output. Python time includes HDF5 decoding and reconstruction; it is a reference timing, not a controlled language speed comparison.",
    }
    output_json = output_json or (ROOT / "results" / "cpp_event_replay_benchmark.json")
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if not keep_intermediates:
        feed_file.unlink(missing_ok=True)
        output_file.unlink(missing_ok=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--stride", type=int, default=100)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--days", type=int)
    parser.add_argument("--events-per-day", type=int)
    parser.add_argument("--keep-intermediates", action="store_true")
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    result = replay(args.data, args.stride, args.repetitions, args.days,
                    args.events_per_day, args.keep_intermediates, args.output_json)
    summary_keys = (
        "dates", "date_start", "date_end", "raw_events", "python_hdf5_reconstruction_seconds",
        "normalized_binary_write_seconds", "cpp_compiler", "cpp_build_optimization",
        "cpp_runs_seconds", "cpp_median_seconds", "cpp_events_per_second_median",
        "matched_snapshots", "mismatches", "processed_events", "aggregate_volume_checks",
        "aggregate_volume_mismatches", "output_sha256",
    )
    print(json.dumps({key: result[key] for key in summary_keys}, indent=2))


if __name__ == "__main__":
    main()
