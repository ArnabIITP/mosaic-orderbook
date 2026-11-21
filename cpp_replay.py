"""Run snapshot sweeps through the C++ order-book matcher and verify Python parity."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from analyze_lobster import replay


ROOT = Path(__file__).resolve().parent
ORDER_SIZES = (100, 500, 1000)


def build_binary() -> Path:
    binary = ROOT / "build" / ("order_book_cli.exe" if sys.platform == "win32" else "order_book_cli")
    result = subprocess.run(
        [sys.executable, str(ROOT / "compare_cpp.py"), "--events", "0", "--binary", str(binary)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("could not build C++ matcher:\n" + result.stderr)
    return binary


def run_snapshot_replay(rows: list[dict[str, Any]], verify_python: bool = True) -> dict[str, float | int]:
    """Execute both sides and all three sizes against each snapshot in C++."""
    binary = build_binary()
    commands = []
    for row in rows:
        fields = ["X", format(row["mid"], ".17g"), str(len(row["asks"]))]
        for price, quantity in row["asks"]:
            fields.extend((str(price), str(quantity)))
        fields.append(str(len(row["bids"])))
        for price, quantity in row["bids"]:
            fields.extend((str(price), str(quantity)))
        fields.append(str(len(ORDER_SIZES)))
        fields.extend(str(size) for size in ORDER_SIZES)
        commands.append(" ".join(fields))

    started = time.perf_counter()
    result = subprocess.run(
        [str(binary)],
        input="\n".join(commands) + "\n",
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    cpp_seconds = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError("C++ snapshot replay failed:\n" + result.stderr)
    output_lines = result.stdout.splitlines()
    if len(output_lines) != len(rows):
        raise AssertionError(f"C++ returned {len(output_lines)} rows for {len(rows)} snapshots")

    max_abs_error = 0.0
    parity_checks = 0
    for row, line in zip(rows, output_lines):
        values = line.split("\t")
        if values[0] != "X" or len(values) != 1 + len(ORDER_SIZES) * 4:
            raise ValueError(f"malformed C++ replay result: {line[:200]}")
        for offset, size in enumerate(ORDER_SIZES):
            base = 1 + offset * 4
            buy_filled, buy_cost = int(values[base]), None if values[base + 1] == "-" else float(values[base + 1])
            sell_filled, sell_cost = int(values[base + 2]), None if values[base + 3] == "-" else float(values[base + 3])
            for side, levels, filled, cost in (
                ("buy", row["asks"], buy_filled, buy_cost),
                ("sell", row["bids"], sell_filled, sell_cost),
            ):
                key = f"{side}_{size}"
                if verify_python:
                    python_cost, python_fill = replay(levels, side, size, row["mid"])
                    if (python_cost is None) != (cost is None):
                        raise AssertionError(
                            f"Python/C++ availability mismatch ticker={row.get('ticker')} "
                            f"time={row.get('time')} side={side} size={size}: "
                            f"Python cost={python_cost}, C++ cost={cost}"
                        )
                    error = 0.0 if python_cost is None else abs(float(python_cost) - float(cost))
                    max_abs_error = max(max_abs_error, error)
                    parity_checks += 1
                    if error > 1e-8 or abs(python_fill - filled / size) > 1e-12:
                        raise AssertionError(
                            f"Python/C++ mismatch ticker={row.get('ticker')} time={row.get('time')} "
                            f"side={side} size={size}: Python ({python_cost}, {python_fill}), "
                            f"C++ ({cost}, {filled / size})"
                        )
                row[f"{key}_bps"] = cost
                row[f"{key}_fill"] = filled / size

    return {
        "snapshots": len(rows),
        "orders": len(rows) * len(ORDER_SIZES) * 2,
        "parity_checks": parity_checks,
        "max_abs_shortfall_error_bps": max_abs_error,
        "cpp_wall_seconds": cpp_seconds,
    }
