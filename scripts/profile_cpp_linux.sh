#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPETITIONS="${1:-200000}"
OUT_DIR="${ROOT_DIR}/build/linux_cpp_profile"

for tool in g++ perf; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "Required Linux tool not found: ${tool}" >&2
    exit 2
  fi
done

mkdir -p "${OUT_DIR}"
g++ -std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic \
  -I "${ROOT_DIR}/cpp" "${ROOT_DIR}/cpp/order_book_benchmark.cpp" \
  -o "${OUT_DIR}/order_book_benchmark"

perf stat -r 7 \
  -e task-clock,cycles,instructions,branches,branch-misses,cache-references,cache-misses \
  -- "${OUT_DIR}/order_book_benchmark" "${REPETITIONS}" \
  > "${OUT_DIR}/benchmark-output.jsonl" 2> "${OUT_DIR}/perf-stat.txt"

perf record -o "${OUT_DIR}/perf.data" -F 999 --call-graph dwarf \
  -- "${OUT_DIR}/order_book_benchmark" "${REPETITIONS}" \
  > /dev/null
perf report --stdio -i "${OUT_DIR}/perf.data" --sort=symbol,dso \
  > "${OUT_DIR}/perf-report.txt"

echo "Wrote profiling artifacts under ${OUT_DIR}"
