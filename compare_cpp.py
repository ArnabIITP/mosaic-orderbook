"""Build the C++ matcher and compare it with Python on one deterministic stream."""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from order_book import OrderBook


ROOT = Path(__file__).resolve().parent


def make_stream(random_events: int, seed: int) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = [
        ("L", "ask_maker", "S", "10100", "5"),
        ("L", "ask_maker", "S", "10100", "3"),
        ("L", "bid_maker", "B", "9900", "10"),
        ("L", "bid_maker", "B", "9800", "8"),
        ("M", "buyer", "B", "6"),
        ("C", "2"),
        ("L", "buyer_limit", "B", "10200", "7"),
        ("L", "ask_maker", "S", "10200", "4"),
        ("M", "seller", "S", "20"),
        ("C", "99999"),
        ("L", "ask_maker", "S", "10000", "9"),
        ("M", "buyer", "B", "4"),
    ]
    rng = random.Random(seed)
    submitted_ids = list(range(1, 11))
    for _ in range(random_events):
        choice = rng.randrange(4)
        if choice == 0:
            commands.append(("L", "bid_maker", "B", str(rng.randrange(9850, 10151)), str(rng.randrange(1, 21))))
            submitted_ids.append(len(submitted_ids) + 1)
        elif choice == 1:
            commands.append(("L", "ask_maker", "S", str(rng.randrange(9850, 10151)), str(rng.randrange(1, 21))))
            submitted_ids.append(len(submitted_ids) + 1)
        elif choice == 2:
            commands.append(("M", "buyer", "B", str(rng.randrange(1, 31))))
            submitted_ids.append(len(submitted_ids) + 1)
        else:
            commands.append(("M", "seller", "S", str(rng.randrange(1, 31))))
            submitted_ids.append(len(submitted_ids) + 1)
        if rng.random() < 0.15:
            commands.append(("C", str(rng.choice(submitted_ids))))
    return commands


def execute_python(commands: list[tuple[str, ...]]) -> tuple[list[tuple], list[tuple]]:
    book = OrderBook()
    responses: list[tuple] = []
    for command in commands:
        if command[0] == "L":
            _, trader, side, price, quantity = command
            order_id, unfilled = book.limit_order(trader, "buy" if side == "B" else "sell", int(price), int(quantity))
            responses.append(("R", order_id, unfilled))
        elif command[0] == "M":
            _, trader, side, quantity = command
            order_id, unfilled = book.market_order(trader, "buy" if side == "B" else "sell", int(quantity))
            responses.append(("R", order_id, unfilled))
        else:
            responses.append(("C", book.cancel(int(command[1]))))

    state: list[tuple] = []
    for price, quantity in book.snapshot(1_000_000)["bids"]:
        state.append(("B", price, quantity))
    for price, quantity in book.snapshot(1_000_000)["asks"]:
        state.append(("A", price, quantity))
    for trade in book.trades:
        state.append(("T", trade.price, trade.quantity, trade.buyer, trade.seller, trade.buy_order_id, trade.sell_order_id))
    for trader, account in sorted(book.accounts.items()):
        state.append(("P", trader, account.inventory, account.cash, account.realised_pnl, account.average_cost))
    state.append(("Z", book.spread, book.midpoint))
    return responses, state


def parse_cpp(stdout: str) -> tuple[list[tuple], list[tuple]]:
    responses: list[tuple] = []
    state: list[tuple] = []
    for line in stdout.splitlines():
        fields = line.split("\t")
        tag = fields[0]
        if tag == "R":
            responses.append((tag, int(fields[1]), int(fields[2])))
        elif tag == "C":
            responses.append((tag, int(fields[1])))
        elif tag in ("B", "A"):
            state.append((tag, int(fields[1]), int(fields[2])))
        elif tag == "T":
            state.append((tag, int(fields[1]), int(fields[2]), fields[3], fields[4], int(fields[5]), int(fields[6])))
        elif tag == "P":
            state.append((tag, fields[1], int(fields[2]), float(fields[3]), float(fields[4]), float(fields[5])))
        elif tag == "Z":
            state.append((tag, None if fields[1] == "-" else int(fields[1]), None if fields[2] == "-" else float(fields[2])))
        else:
            raise ValueError(f"unknown C++ output record: {line}")
    return responses, state


def compare_state(expected: list[tuple], actual: list[tuple]) -> None:
    if len(expected) != len(actual):
        raise AssertionError(f"state record count differs: Python {len(expected)}, C++ {len(actual)}")
    for index, (left, right) in enumerate(zip(expected, actual)):
        if len(left) != len(right) or left[0] != right[0]:
            raise AssertionError(f"state record {index} differs: {left} != {right}")
        for a, b in zip(left[1:], right[1:]):
            if isinstance(a, float) or isinstance(b, float):
                if a is None or b is None or abs(a - b) > 1e-8:
                    raise AssertionError(f"state record {index} differs: {left} != {right}")
            elif a != b:
                raise AssertionError(f"state record {index} differs: {left} != {right}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=2_000, help="number of deterministic randomized events")
    parser.add_argument("--seed", type=int, default=29)
    parser.add_argument("--compiler", help="C++17 compiler executable; when omitted, tries C++ compilers on PATH")
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--optimization", choices=("-O0", "-O1", "-O2", "-O3"), default="-O2")
    args = parser.parse_args()
    if args.events < 0:
        parser.error("--events must be non-negative")

    binary = args.binary or ROOT / "build" / ("order_book_cli.exe" if sys.platform == "win32" else "order_book_cli")
    binary.parent.mkdir(parents=True, exist_ok=True)
    if args.compiler:
        candidates = [args.compiler]
    else:
        candidates = []
        executable = "g++.exe" if sys.platform == "win32" else "g++"
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory) / executable
            if candidate.exists() and str(candidate) not in candidates:
                candidates.append(str(candidate))
        if not candidates:
            candidates = ["g++"]

    compile_errors = []
    for candidate in candidates:
        compiler_path = shutil.which(candidate) or candidate
        compiler_dir = str(Path(compiler_path).resolve().parent)
        compiler_env = os.environ.copy()
        compiler_env["PATH"] = compiler_dir + os.pathsep + compiler_env.get("PATH", "")
        compile_result = subprocess.run(
            [compiler_path, "-std=c++17", args.optimization, "-DNDEBUG", "-Wall", "-Wextra", "-Wpedantic", str(ROOT / "cpp" / "order_book_cli.cpp"), "-o", str(binary)],
            cwd=ROOT,
            env=compiler_env,
            text=True,
            capture_output=True,
        )
        if compile_result.returncode == 0:
            break
        compile_errors.append(f"{compiler_path}:\n{compile_result.stderr}")
    else:
        raise RuntimeError("C++ build failed for available compiler(s):\n" + "\n".join(compile_errors))

    commands = make_stream(args.events, args.seed)
    cpp_input = "\n".join(" ".join(command) for command in commands) + "\nD\n"
    result = subprocess.run([str(binary)], input=cpp_input, cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"C++ replay failed:\n{result.stderr}")

    expected_responses, expected_state = execute_python(commands)
    actual_responses, actual_state = parse_cpp(result.stdout)
    if len(expected_responses) != len(actual_responses):
        raise AssertionError(f"response counts differ: Python {len(expected_responses)}, C++ {len(actual_responses)}")
    if expected_responses != actual_responses:
        mismatch = next((i for i, pair in enumerate(zip(expected_responses, actual_responses)) if pair[0] != pair[1]), None)
        raise AssertionError(f"action response mismatch at command {mismatch}: Python {expected_responses[mismatch]} vs C++ {actual_responses[mismatch]}")
    compare_state(expected_state, actual_state)
    print(f"Parity passed: {len(commands)} actions, {len(expected_state)} final-state records, seed={args.seed}.")
    print(f"C++ compiler: {compiler_path} ({args.optimization})")
    print(f"C++ executable: {binary}")


if __name__ == "__main__":
    main()
