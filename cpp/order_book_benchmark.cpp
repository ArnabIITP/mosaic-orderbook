#include "order_book.hpp"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
using Ns = std::chrono::nanoseconds;

template <class Fn>
void benchmark(const std::string& name, std::uint64_t repetitions, Fn&& operation) {
    const std::uint64_t sample_every = std::max<std::uint64_t>(1, repetitions / 10'000);
    std::vector<std::uint64_t> samples;
    samples.reserve(static_cast<std::size_t>(repetitions / sample_every + 1));
    const auto start = Clock::now();
    for (std::uint64_t i = 0; i < repetitions; ++i) {
        if (i % sample_every == 0) {
            const auto before = Clock::now();
            operation(i);
            const auto after = Clock::now();
            samples.push_back(static_cast<std::uint64_t>(std::chrono::duration_cast<Ns>(after - before).count()));
        } else {
            operation(i);
        }
    }
    const auto finish = Clock::now();
    std::sort(samples.begin(), samples.end());
    const auto percentile = [&samples](double p) {
        const auto index = static_cast<std::size_t>(p * static_cast<double>(samples.size() - 1));
        return samples[index];
    };
    const auto elapsed_ns = std::chrono::duration_cast<Ns>(finish - start).count();
    std::cout << "{\"operation\":\"" << name << "\",\"repetitions\":" << repetitions
              << ",\"sample_count\":" << samples.size()
              << ",\"elapsed_ns\":" << elapsed_ns
              << ",\"ops_per_second\":" << (static_cast<double>(repetitions) * 1e9 / elapsed_ns)
              << ",\"sampled_latency_ns\":{\"p50\":" << percentile(.50)
              << ",\"p95\":" << percentile(.95) << ",\"p99\":" << percentile(.99)
              << ",\"p999\":" << percentile(.999) << "}}\n";
}

void run(std::uint64_t repetitions) {
    lob::OrderBook insert_cancel_book;
    benchmark("limit_insert_cancel_cycle", repetitions, [&insert_cancel_book](std::uint64_t) {
        const auto order = insert_cancel_book.limit_order("maker", lob::Side::Buy, 10000, 1);
        if (insert_cancel_book.cancel(order.order_id) != 1) std::terminate();
    });

    lob::OrderBook cancel_book;
    std::vector<lob::OrderId> ids;
    ids.reserve(static_cast<std::size_t>(repetitions));
    for (std::uint64_t i = 0; i < repetitions; ++i)
        ids.push_back(cancel_book.restore_limit_order("maker", lob::Side::Buy, 10000, 1));
    benchmark("fifo_queue_cancel", repetitions, [&cancel_book, &ids](std::uint64_t i) {
        if (cancel_book.cancel(ids[static_cast<std::size_t>(i)]) != 1) std::terminate();
    });

    lob::OrderBook trade_book;
    trade_book.restore_limit_order("ask-maker", lob::Side::Sell, 10001,
                                   static_cast<lob::Quantity>(repetitions) + 1);
    benchmark("market_order_single_level_match", repetitions, [&trade_book](std::uint64_t) {
        const auto result = trade_book.market_order("taker", lob::Side::Buy, 1);
        if (result.unfilled != 0) std::terminate();
    });

    lob::OrderBook snapshot_book;
    for (int level = 0; level < 10; ++level) {
        for (int order = 0; order < 20; ++order) {
            snapshot_book.restore_limit_order("bid", lob::Side::Buy, 10000 - level, 10);
            snapshot_book.restore_limit_order("ask", lob::Side::Sell, 10001 + level, 10);
        }
    }
    benchmark("top5_snapshot", repetitions, [&snapshot_book](std::uint64_t) {
        const auto bids = snapshot_book.snapshot(lob::Side::Buy, 5);
        const auto asks = snapshot_book.snapshot(lob::Side::Sell, 5);
        if (bids.size() != 5 || asks.size() != 5) std::terminate();
    });
}
}  // namespace

int main(int argc, char** argv) {
    const std::uint64_t repetitions = argc > 1 ? std::stoull(argv[1]) : 200'000;
    if (repetitions < 1000) {
        std::cerr << "repetitions must be at least 1000\n";
        return 2;
    }
    run(repetitions);
    return 0;
}
