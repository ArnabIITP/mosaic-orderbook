"""Compile and repeatedly measure isolated C++ order-book operations."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "cpp" / "order_book_benchmark.cpp"
DEFAULT_OUTPUT = ROOT / "results" / "cpp_operation_benchmark.json"
DEFAULT_COMPILER = ("C:/msys64/ucrt64/bin/g++.exe" if platform.system() == "Windows"
                    and Path("C:/msys64/ucrt64/bin/g++.exe").exists() else shutil.which("g++") or "g++")


def run(compiler: str, repetitions: int, runs: int, output: Path) -> dict[str, Any]:
    if repetitions < 1000 or runs < 3:
        raise ValueError("Use at least 1,000 operations and three measured repetitions")
    build_dir = ROOT / "build" / "operation_benchmark"
    build_dir.mkdir(parents=True, exist_ok=True)
    binary = build_dir / ("order_book_benchmark.exe" if platform.system() == "Windows" else "order_book_benchmark")
    compile_command = [compiler, "-std=c++17", "-O3", "-DNDEBUG", "-Wall", "-Wextra", "-Wpedantic",
                       "-I", str(ROOT / "cpp"), str(SOURCE), "-o", str(binary)]
    compiler_dir = str(Path(compiler).resolve().parent) if ("/" in compiler or "\\" in compiler) else ""
    compiler_env = os.environ.copy()
    if compiler_dir:
        compiler_env["PATH"] = compiler_dir + os.pathsep + compiler_env.get("PATH", "")
    build = subprocess.run(compile_command, capture_output=True, text=True, env=compiler_env)
    if build.returncode:
        raise RuntimeError(f"C++ benchmark build failed:\n{build.stderr}")
    version = subprocess.run([compiler, "--version"], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    def invoke(count: int) -> list[dict[str, Any]]:
        completed = subprocess.run([str(binary), str(count)], check=True,
                                   capture_output=True, text=True, env=compiler_env)
        return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]

    invoke(max(1000, repetitions // 20))  # warm-up only; excluded from reported runs
    measured = [invoke(repetitions) for _ in range(runs)]
    operations = [row["operation"] for row in measured[0]]
    summary: dict[str, Any] = {}
    for name in operations:
        rows = [next(row for row in trial if row["operation"] == name) for trial in measured]
        summary[name] = {
            "repetitions_per_run": repetitions,
            "runs": runs,
            "ops_per_second_median": statistics.median(row["ops_per_second"] for row in rows),
            "ops_per_second_min": min(row["ops_per_second"] for row in rows),
            "ops_per_second_max": max(row["ops_per_second"] for row in rows),
            "sampled_latency_ns_median_of_run_percentiles": {
                key: statistics.median(row["sampled_latency_ns"][key] for row in rows)
                for key in ("p50", "p95", "p99", "p999")},
            "sample_count_per_run": rows[0]["sample_count"],
            "run_elapsed_ms": [row["elapsed_ns"] / 1e6 for row in rows],
        }
    result = {
        "benchmark": "MOSAIC C++ order-book operation microbenchmark",
        "host": {"platform": platform.platform(), "machine": platform.machine(),
                 "processor": platform.processor(), "python": platform.python_version()},
        "compiler": version,
        "build_flags": "-std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic",
        "measurement": {
            "method": "one warm-up process, then repeated in-process operation loops; steady_clock samples one operation per repetitions/10,000",
            "latency_caveat": "Sampled latency includes clock-read overhead and operation-specific allocation/accounting; throughput is a separate whole-loop measurement.",
            "operation_scopes": {
                "limit_insert_cancel_cycle": "Limit order insertion followed immediately by cancellation on a persistent book.",
                "fifo_queue_cancel": "Cancel one order from a pre-populated single-price FIFO queue; queue setup is outside timing.",
                "market_order_single_level_match": "One-share market order matched against one resting price level, including trade/account updates.",
                "top5_snapshot": "Aggregate and return both five-level book sides from a persistent 20-level, 400-order book."}},
        "operations": summary,
        "raw_runs": measured,
        "compile_stderr": build.stderr.strip(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report = ["# C++ Order-Book Operation Benchmark", "",
        f"Host: {platform.platform()} ({platform.processor() or platform.machine()})  ",
        f"Compiler: {version}  ", f"Build: `{result['build_flags']}`  ",
        f"Workload: {repetitions:,} operations per run, {runs} measured runs after a warm-up. Timing overhead and scope caveats are recorded in the JSON artifact.", "",
        "| Operation | Median throughput (ops/s) | Run range (ops/s) | Sampled p50 ns | p95 ns | p99 ns | p99.9 ns | Samples/run |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, data in summary.items():
        lat = data["sampled_latency_ns_median_of_run_percentiles"]
        report.append(f"| `{name}` | {data['ops_per_second_median']:,.0f} | {data['ops_per_second_min']:,.0f}–{data['ops_per_second_max']:,.0f} | {lat['p50']:,} | {lat['p95']:,} | {lat['p99']:,} | {lat['p999']:,} | {data['sample_count_per_run']:,} |")
    report.extend(["", "These are isolated in-process data-structure measurements, not exchange, network, feed-decoding, or end-to-end latency. The measured per-operation samples include timer overhead. Compare operation classes and repeatability on this host; do not compare these numbers directly with the full-feed replay benchmark.", "",
        "Reproduce with `python benchmark_cpp_operations.py --repetitions 200000 --runs 7`. Linux hardware-counter instructions are in [docs/cpp_performance.md](../docs/cpp_performance.md); this report does not claim Linux results.", ""])
    report_path = output.with_suffix(".md")
    report_path.write_text("\n".join(report), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default=DEFAULT_COMPILER)
    parser.add_argument("--repetitions", type=int, default=200_000)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(args.compiler, args.repetitions, args.runs, args.output)
    print(json.dumps({"host": result["host"], "compiler": result["compiler"],
                      "operations": result["operations"], "outputs": [str(args.output), str(args.output.with_suffix('.md'))]}, indent=2))


if __name__ == "__main__":
    main()
