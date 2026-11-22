# C++ Order-Book Operation Benchmark

Host: Windows-11-10.0.26300-SP0 (Intel64 Family 6 Model 154 Stepping 3, GenuineIntel)<br>
Compiler: g++.exe (Rev2, Built by MSYS2 project) 14.2.0<br>
Build: `-std=c++17 -O3 -DNDEBUG -Wall -Wextra -Wpedantic`<br>
Workload: 200,000 operations per run, 7 measured runs after a warm-up. Timing overhead and scope caveats are recorded in the JSON artifact.

| Operation | Median throughput (ops/s) | Run range (ops/s) | Sampled p50 ns | p95 ns | p99 ns | p99.9 ns | Samples/run |
|---|---:|---:|---:|---:|---:|---:|---:|
| `limit_insert_cancel_cycle` | 3,820,010 | 3,376,510–4,855,340 | 300 | 500 | 700 | 900 | 10,000 |
| `fifo_queue_cancel` | 8,872,600 | 6,375,580–9,897,950 | 100 | 200 | 400 | 1,300 | 10,000 |
| `market_order_single_level_match` | 5,444,490 | 4,084,930–6,621,290 | 100 | 200 | 700 | 10,300 | 10,000 |
| `top5_snapshot` | 1,498,330 | 1,225,060–1,644,500 | 600 | 1,200 | 1,600 | 2,700 | 10,000 |

These are isolated in-process data-structure measurements, not exchange, network, feed-decoding, or end-to-end latency. The measured per-operation samples include timer overhead. Compare operation classes and repeatability on this host; do not compare these numbers directly with the full-feed replay benchmark.

Reproduce with `python benchmark_cpp_operations.py --repetitions 200000 --runs 7`. Linux hardware-counter instructions are in [docs/cpp_performance.md](../docs/cpp_performance.md); this report does not claim Linux results.
