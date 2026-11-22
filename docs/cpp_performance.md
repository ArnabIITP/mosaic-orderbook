# C++ Performance Measurement

MOSAIC reports two different measurements:

- `results/cpp_event_replay_benchmark.json` measures a full historical feed replay, including binary feed I/O, decoding, book updates, and snapshot output.
- `results/cpp_operation_benchmark.md` isolates order insertion/cancellation, FIFO cancellation, one-level market matching, and top-five snapshots. Its sampled per-call latency includes clock-read overhead; use throughput and latency as separate measurements.

## Reproduce the in-process operation benchmark

On Windows with MSYS2 UCRT64, or on Linux with GCC:

```sh
python benchmark_cpp_operations.py --repetitions 200000 --runs 7
```

The benchmark writes JSON and Markdown under `results/`; the compiled binary goes under ignored `build/`.

## Linux hardware-counter profile

Run on an otherwise idle Linux host with GCC, Linux `perf`, and permission to access performance counters:

```sh
uname -a
lscpu
g++ --version
bash scripts/profile_cpp_linux.sh 200000
```

The script saves `perf stat`, `perf record`, and symbol reports to ignored `build/linux_cpp_profile/`. Record the CPU model, kernel, compiler, flags, and whether the host is a VM before comparing runs. Linux `perf` can be restricted by kernel or container policy even when installed. The current local session is Windows; its WSL image did not provide `g++` or `perf`, so no Linux counter results are claimed in the checked-in benchmark.
