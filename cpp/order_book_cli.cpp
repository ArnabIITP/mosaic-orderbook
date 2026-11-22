#include "order_book.hpp"

#include <array>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <vector>

namespace {

struct ExecutionSummary {
    lob::Quantity filled;
    double vwap;
    double shortfall_bps;
};

struct FeedOrder {
    lob::OrderId internal_id;
    lob::Side side;
    lob::Price price;
    lob::Quantity quantity;
    std::string order_type;
};
using FeedKey = std::pair<std::int64_t, std::int64_t>;

bool is_resting(int side, lob::Price price, lob::Quantity quantity, const std::string& type) {
    return (side == 1 || side == 2 || side == 5) && price > 0 && quantity > 0 &&
           (type == "2" || type == "P" || type == "K");
}

lob::Side feed_side(int side) { return side == 1 ? lob::Side::Buy : lob::Side::Sell; }

lob::OrderId add_feed_order(lob::OrderBook& book, int side, lob::Price price,
                            lob::Quantity quantity, const std::string& type) {
    return book.restore_limit_order("feed", feed_side(side), price, quantity);
}

void amend_feed_order(lob::OrderBook& book, std::map<FeedKey, FeedOrder>& orders,
                      const FeedKey& key, int side, lob::Price price,
                      lob::Quantity quantity, const std::string& type, bool count_missing,
                      std::uint64_t& missing_updates) {
    auto found = orders.find(key);
    if (found == orders.end()) {
        if (count_missing) ++missing_updates;
        if (is_resting(side, price, quantity, type)) {
            orders.emplace(key, FeedOrder{add_feed_order(book, side, price, quantity, type),
                                           feed_side(side), price, quantity, type});
        }
        return;
    }
    FeedOrder old = found->second;
    const int new_side_raw = (side == 1 || side == 2 || side == 5) ? side : (old.side == lob::Side::Buy ? 1 : 2);
    const lob::Price new_price = price == -1 ? old.price : price;
    const lob::Quantity new_quantity = quantity == -1 ? old.quantity : quantity;
    const std::string new_type = type == "-1" ? old.order_type : type;
    if (!is_resting(new_side_raw, new_price, new_quantity, new_type)) {
        book.cancel(old.internal_id);
        orders.erase(found);
        return;
    }
    const lob::Side new_side = feed_side(new_side_raw);
    if (new_price != old.price || new_side != old.side) {
        book.cancel(old.internal_id);
        found->second = FeedOrder{add_feed_order(book, new_side_raw, new_price, new_quantity, new_type),
                                  new_side, new_price, new_quantity, new_type};
    } else {
        book.modify_remaining(old.internal_id, new_quantity);
        found->second.quantity = new_quantity;
        found->second.order_type = new_type;
    }
}

void emit_feed_snapshot(const lob::OrderBook& book, const std::string& date,
                        std::int64_t event_index, std::int64_t timestamp) {
    const auto asks = book.snapshot(lob::Side::Sell, 10);
    const auto bids = book.snapshot(lob::Side::Buy, 10);
    if (asks.empty() || bids.empty() || asks.front().price <= bids.front().price) return;
    std::cout << "S\t" << date << '\t' << event_index << '\t' << timestamp << '\t' << asks.size();
    for (const auto& level : asks) std::cout << '\t' << level.price << '\t' << level.quantity;
    std::cout << '\t' << bids.size();
    for (const auto& level : bids) std::cout << '\t' << level.price << '\t' << level.quantity;
    std::cout << '\n';
}

void run_feed_event(std::istringstream& input, lob::OrderBook& book,
                    std::map<FeedKey, FeedOrder>& orders, std::uint64_t& missing_updates) {
    std::string date, action, order_type;
    std::int64_t event_index, timestamp, order_date, order_id;
    int side;
    lob::Price price;
    lob::Quantity quantity;
    if (!(input >> date >> event_index >> timestamp >> action >> order_date >> order_id >> side >> price >> quantity >> order_type))
        throw std::invalid_argument("malformed E command");
    if (action == "F") {
        book.clear();
        orders.clear();
    } else if (action == "A") {
        if (is_resting(side, price, quantity, order_type)) {
            const FeedKey key{order_date, order_id};
            const auto found = orders.find(key);
            if (found != orders.end()) {
                book.cancel(found->second.internal_id);
                orders.erase(found);
            }
            orders.emplace(key, FeedOrder{add_feed_order(book, side, price, quantity, order_type),
                                           feed_side(side), price, quantity, order_type});
        }
    } else if (action == "Y" || action == "M") {
        amend_feed_order(book, orders, FeedKey{order_date, order_id}, side, price, quantity,
                         order_type, action == "M", missing_updates);
    } else if (action == "D") {
        const auto found = orders.find(FeedKey{order_date, order_id});
        if (found != orders.end()) {
            book.cancel(found->second.internal_id);
            orders.erase(found);
        }
    } else {
        throw std::invalid_argument("unsupported feed action: " + action);
    }
}

std::uint64_t decode_unsigned(const std::array<unsigned char, 51>& bytes, std::size_t offset, std::size_t count) {
    std::uint64_t value = 0;
    for (std::size_t i = 0; i < count; ++i) value |= static_cast<std::uint64_t>(bytes[offset + i]) << (8 * i);
    return value;
}

std::int64_t decode_signed(const std::array<unsigned char, 51>& bytes, std::size_t offset, std::size_t count) {
    std::uint64_t value = decode_unsigned(bytes, offset, count);
    if (count < 8 && (value & (std::uint64_t{1} << (count * 8 - 1)))) value |= (~std::uint64_t{0}) << (count * 8);
    return static_cast<std::int64_t>(value);
}

int run_binary_feed(const std::string& path, std::size_t stride, std::size_t profile_every = 0) {
    if (stride == 0) throw std::invalid_argument("stride must be positive");
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("could not open binary feed: " + path);
    lob::OrderBook book;
    std::map<FeedKey, FeedOrder> orders;
    std::uint64_t missing_updates = 0;
    bool sample_pending = false;
    bool have_group = false;
    std::uint32_t date_index = 0;
    std::int64_t event_index = -1, timestamp = 0;
    std::array<unsigned char, 51> bytes{};
    std::uint64_t processed = 0;
    std::vector<std::uint64_t> mutation_samples_ns;
    while (input.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()))) {
        const auto next_date = static_cast<std::uint32_t>(decode_unsigned(bytes, 0, 4));
        const auto next_index = static_cast<std::uint32_t>(decode_unsigned(bytes, 4, 4));
        const auto next_timestamp = decode_signed(bytes, 8, 8);
        if (have_group && (next_date != date_index || next_timestamp != timestamp)) {
            if (sample_pending) emit_feed_snapshot(book, std::to_string(date_index), event_index, timestamp);
            sample_pending = false;
        }
        date_index = next_date;
        event_index = next_index;
        timestamp = next_timestamp;
        have_group = true;
        const char action_char = static_cast<char>(bytes[16]);
        const std::string action(1, action_char);
        const auto order_date = decode_signed(bytes, 17, 8);
        const auto order_id = decode_signed(bytes, 25, 8);
        const int side = static_cast<std::int8_t>(bytes[33]);
        const auto price = decode_signed(bytes, 34, 8);
        const auto quantity = decode_signed(bytes, 42, 8);
        const char type_char = static_cast<char>(bytes[50]);
        const std::string type = type_char == '-' ? "-1" : std::string(1, type_char);
        const FeedKey key{order_date, order_id};

        const bool sample_mutation = profile_every > 0 && processed % profile_every == 0;
        const auto mutation_started = sample_mutation ? std::chrono::steady_clock::now()
                                                      : std::chrono::steady_clock::time_point{};
        if (action == "F") {
            book.clear();
            orders.clear();
        } else if (action == "A") {
            if (is_resting(side, price, quantity, type)) {
                const auto found = orders.find(key);
                if (found != orders.end()) {
                    book.cancel(found->second.internal_id);
                    orders.erase(found);
                }
                orders.emplace(key, FeedOrder{add_feed_order(book, side, price, quantity, type),
                                               feed_side(side), price, quantity, type});
            }
        } else if (action == "Y" || action == "M") {
            amend_feed_order(book, orders, key, side, price, quantity, type, action == "M", missing_updates);
        } else if (action == "D") {
            const auto found = orders.find(key);
            if (found != orders.end()) {
                book.cancel(found->second.internal_id);
                orders.erase(found);
            }
        } else {
            throw std::runtime_error("unsupported binary feed action");
        }
        if (sample_mutation) {
            const auto elapsed = std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - mutation_started).count();
            mutation_samples_ns.push_back(static_cast<std::uint64_t>(elapsed));
        }
        if (event_index % static_cast<std::int64_t>(stride) == 0) sample_pending = true;
        ++processed;
    }
    if (input.gcount() != 0) throw std::runtime_error("truncated binary feed record");
    if (sample_pending) emit_feed_snapshot(book, std::to_string(date_index), event_index, timestamp);
    std::cout << "Q\tprocessed_events\t" << processed << "\tmissing_modify_updates\t" << missing_updates << '\n';
    if (profile_every > 0 && !mutation_samples_ns.empty()) {
        std::sort(mutation_samples_ns.begin(), mutation_samples_ns.end());
        const auto quantile = [&mutation_samples_ns](double q) {
            const auto rank = static_cast<std::size_t>(std::ceil(q * mutation_samples_ns.size()));
            return mutation_samples_ns[std::max<std::size_t>(1, rank) - 1];
        };
        std::cerr << "PROFILE\tmutation_every\t" << profile_every
                  << "\tmutation_samples\t" << mutation_samples_ns.size()
                  << "\tmutation_p50_ns\t" << quantile(0.50)
                  << "\tmutation_p95_ns\t" << quantile(0.95)
                  << "\tmutation_p99_ns\t" << quantile(0.99)
                  << "\tmutation_p999_ns\t" << quantile(0.999) << '\n';
    }
    return 0;
}

ExecutionSummary replay_snapshot(lob::Side incoming_side, lob::Quantity requested, double midpoint,
                                 const std::vector<lob::Level>& opposing_levels) {
    lob::OrderBook book;
    const lob::Side resting_side = incoming_side == lob::Side::Buy ? lob::Side::Sell : lob::Side::Buy;
    std::size_t index = 0;
    for (const auto& level : opposing_levels) {
        book.limit_order("snapshot-maker-" + std::to_string(index++), resting_side, level.price, level.quantity);
    }
    book.market_order("snapshot-taker", incoming_side, requested);
    lob::Quantity filled = 0;
    long double notional = 0.0L;
    for (const auto& trade : book.trades()) {
        filled += trade.quantity;
        notional += static_cast<long double>(trade.price) * static_cast<long double>(trade.quantity);
    }
    if (filled == 0) return {0, 0.0, 0.0};
    const double vwap = static_cast<double>(notional / static_cast<long double>(filled));
    const double signed_cost = incoming_side == lob::Side::Buy ? vwap - midpoint : midpoint - vwap;
    return {filled, vwap, signed_cost / midpoint * 10000.0};
}

void run_snapshot_query(std::istringstream& input) {
    double midpoint;
    std::size_t ask_count, bid_count, size_count;
    if (!(input >> midpoint >> ask_count)) throw std::invalid_argument("malformed X command header");
    std::vector<lob::Level> asks, bids;
    for (std::size_t i = 0; i < ask_count; ++i) {
        lob::Price price;
        lob::Quantity quantity;
        if (!(input >> price >> quantity) || price <= 0 || quantity <= 0) throw std::invalid_argument("invalid ask level in X command");
        asks.push_back({price, quantity});
    }
    if (!(input >> bid_count)) throw std::invalid_argument("missing bid level count in X command");
    for (std::size_t i = 0; i < bid_count; ++i) {
        lob::Price price;
        lob::Quantity quantity;
        if (!(input >> price >> quantity) || price <= 0 || quantity <= 0) throw std::invalid_argument("invalid bid level in X command");
        bids.push_back({price, quantity});
    }
    if (!(input >> size_count) || size_count == 0) throw std::invalid_argument("missing order sizes in X command");
    std::vector<lob::Quantity> sizes(size_count);
    for (auto& size : sizes) {
        if (!(input >> size) || size <= 0) throw std::invalid_argument("order sizes must be positive");
    }
    std::cout << "X";
    for (const auto size : sizes) {
        const auto buy = replay_snapshot(lob::Side::Buy, size, midpoint, asks);
        std::cout << '\t' << buy.filled << '\t';
        if (buy.filled) std::cout << buy.shortfall_bps;
        else std::cout << "-";
        const auto sell = replay_snapshot(lob::Side::Sell, size, midpoint, bids);
        std::cout << '\t' << sell.filled << '\t';
        if (sell.filled) std::cout << sell.shortfall_bps;
        else std::cout << "-";
    }
    std::cout << '\n';
}

void dump(const lob::OrderBook& book) {
    for (const auto& level : book.snapshot(lob::Side::Buy, 1000000))
        std::cout << "B\t" << level.price << '\t' << level.quantity << '\n';
    for (const auto& level : book.snapshot(lob::Side::Sell, 1000000))
        std::cout << "A\t" << level.price << '\t' << level.quantity << '\n';
    for (const auto& trade : book.trades())
        std::cout << "T\t" << trade.price << '\t' << trade.quantity << '\t' << trade.buyer << '\t'
                  << trade.seller << '\t' << trade.buy_order_id << '\t' << trade.sell_order_id << '\n';
    for (const auto& [trader, account] : book.accounts())
        std::cout << "P\t" << trader << '\t' << account.inventory << '\t' << account.cash << '\t'
                  << account.realised_pnl << '\t' << account.average_cost << '\n';
    const auto spread = book.spread();
    const auto midpoint = book.midpoint();
    std::cout << "Z\t";
    if (spread) std::cout << *spread;
    else std::cout << "-";
    std::cout << '\t';
    if (midpoint) std::cout << *midpoint;
    else std::cout << "-";
    std::cout << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    std::cout << std::setprecision(17);
    if (argc == 4 && std::string(argv[1]) == "--replay-events") {
        try {
            return run_binary_feed(argv[2], static_cast<std::size_t>(std::stoull(argv[3])));
        } catch (const std::exception& error) {
            std::cerr << "order_book_cli: " << error.what() << '\n';
            return 2;
        }
    }
    if (argc == 6 && std::string(argv[1]) == "--replay-events" &&
        std::string(argv[4]) == "--profile-every") {
        try {
            return run_binary_feed(argv[2], static_cast<std::size_t>(std::stoull(argv[3])),
                                   static_cast<std::size_t>(std::stoull(argv[5])));
        } catch (const std::exception& error) {
            std::cerr << "error: " << error.what() << '\n';
            return 1;
        }
    }
    lob::OrderBook book;
    std::map<FeedKey, FeedOrder> feed_orders;
    std::uint64_t missing_feed_updates = 0;
    std::size_t feed_stride = 0;
    bool feed_mode = false;
    bool sample_pending = false;
    std::string feed_date;
    std::int64_t feed_time = 0;
    std::int64_t feed_event_index = -1;
    std::string line;
    try {
        while (std::getline(std::cin, line)) {
            if (line.empty()) continue;
            std::istringstream input(line);
            std::string command;
            input >> command;
            if (command == "L") {
                std::string trader, side;
                lob::Price price;
                lob::Quantity quantity;
                if (!(input >> trader >> side >> price >> quantity)) throw std::invalid_argument("malformed L command");
                const auto result = book.limit_order(trader, lob::parse_side(side), price, quantity);
                std::cout << "R\t" << result.order_id << '\t' << result.unfilled << '\n';
            } else if (command == "M") {
                std::string trader, side;
                lob::Quantity quantity;
                if (!(input >> trader >> side >> quantity)) throw std::invalid_argument("malformed M command");
                const auto result = book.market_order(trader, lob::parse_side(side), quantity);
                std::cout << "R\t" << result.order_id << '\t' << result.unfilled << '\n';
            } else if (command == "C") {
                lob::OrderId id;
                if (!(input >> id)) throw std::invalid_argument("malformed C command");
                std::cout << "C\t" << book.cancel(id) << '\n';
            } else if (command == "X") {
                run_snapshot_query(input);
            } else if (command == "W") {
                if (!(input >> feed_stride) || feed_stride == 0) throw std::invalid_argument("W stride must be positive");
                feed_mode = true;
            } else if (command == "E") {
                if (!feed_mode) throw std::invalid_argument("E feed event requires W stride header");
                std::istringstream header(line);
                std::string tag, date;
                std::int64_t event_index, timestamp;
                if (!(header >> tag >> date >> event_index >> timestamp)) throw std::invalid_argument("malformed E command header");
                if (!feed_date.empty() && (date != feed_date || timestamp != feed_time)) {
                    if (sample_pending) emit_feed_snapshot(book, feed_date, feed_event_index, feed_time);
                    sample_pending = false;
                }
                feed_date = date;
                feed_time = timestamp;
                feed_event_index = event_index;
                run_feed_event(input, book, feed_orders, missing_feed_updates);
                if (static_cast<std::size_t>(event_index) % feed_stride == 0) sample_pending = true;
            } else if (command == "D") {
                dump(book);
            } else {
                throw std::invalid_argument("unknown command: " + command);
            }
        }
        if (feed_mode) {
            if (sample_pending) emit_feed_snapshot(book, feed_date, feed_event_index, feed_time);
            std::cout << "Q\tmissing_modify_updates\t" << missing_feed_updates << '\n';
        }
    } catch (const std::exception& error) {
        std::cerr << "order_book_cli: " << error.what() << '\n';
        return 2;
    }
    return 0;
}
