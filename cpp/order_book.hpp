#pragma once

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <iterator>
#include <list>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace lob {

using Price = std::int64_t;
using Quantity = std::int64_t;
using OrderId = std::uint64_t;

enum class Side { Buy, Sell };

inline Side parse_side(const std::string& value) {
    if (value == "B" || value == "buy") return Side::Buy;
    if (value == "S" || value == "sell") return Side::Sell;
    throw std::invalid_argument("side must be B/buy or S/sell");
}

inline const char* side_name(Side side) { return side == Side::Buy ? "B" : "S"; }

struct Account {
    Quantity inventory = 0;
    double cash = 0.0;
    double realised_pnl = 0.0;
    double average_cost = 0.0;

    void apply_fill(Side side, Price price, Quantity quantity) {
        const Quantity signed_quantity = side == Side::Buy ? quantity : -quantity;
        const Quantity old_inventory = inventory;
        const Quantity new_inventory = old_inventory + signed_quantity;
        cash -= static_cast<double>(signed_quantity) * static_cast<double>(price);

        if (old_inventory == 0 || (old_inventory > 0) == (signed_quantity > 0)) {
            average_cost = (static_cast<double>(std::llabs(old_inventory)) * average_cost +
                            static_cast<double>(quantity) * static_cast<double>(price)) /
                           static_cast<double>(std::llabs(new_inventory));
        } else {
            const Quantity closed = std::min<Quantity>(std::llabs(old_inventory), quantity);
            realised_pnl += static_cast<double>(closed) * (static_cast<double>(price) - average_cost) *
                            (old_inventory > 0 ? 1.0 : -1.0);
            if (new_inventory == 0) {
                average_cost = 0.0;
            } else if ((old_inventory > 0) != (new_inventory > 0)) {
                average_cost = static_cast<double>(price);
            }
        }
        inventory = new_inventory;
    }

    double unrealised_pnl(double mark) const {
        return static_cast<double>(inventory) * (mark - average_cost);
    }
    double equity_pnl(double mark) const { return realised_pnl + unrealised_pnl(mark); }
};

struct Trade {
    Price price;
    Quantity quantity;
    std::string buyer;
    std::string seller;
    OrderId buy_order_id;
    OrderId sell_order_id;
};

struct OrderResult {
    OrderId order_id;
    Quantity unfilled;
};

struct Level {
    Price price;
    Quantity quantity;
};

struct Order {
    OrderId id;
    std::string trader;
    Side side;
    Price price;
    Quantity quantity;
    Quantity remaining;
    std::list<Order*>::iterator queue_position;
};

class OrderBook {
public:
    OrderResult limit_order(const std::string& trader, Side side, Price price, Quantity quantity) {
        return submit(trader, side, quantity, price);
    }

    OrderResult market_order(const std::string& trader, Side side, Quantity quantity) {
        return submit(trader, side, quantity, std::nullopt);
    }

    // Rebuild an externally maintained book without triggering matching.
    OrderId restore_limit_order(const std::string& trader, Side side, Price price, Quantity quantity) {
        if (quantity <= 0) throw std::invalid_argument("quantity must be positive");
        if (price <= 0) throw std::invalid_argument("price must be positive");
        if (next_id_ == 0) throw std::overflow_error("order ID space exhausted");
        const OrderId id = next_id_++;
        auto order = std::make_unique<Order>();
        order->id = id;
        order->trader = trader;
        order->side = side;
        order->price = price;
        order->quantity = quantity;
        order->remaining = quantity;
        Order* raw = order.get();
        if (side == Side::Buy) {
            auto& queue = bids_[price];
            queue.push_back(raw);
            raw->queue_position = std::prev(queue.end());
        } else {
            auto& queue = asks_[price];
            queue.push_back(raw);
            raw->queue_position = std::prev(queue.end());
        }
        orders_.emplace(id, std::move(order));
        return id;
    }

    bool modify_remaining(OrderId id, Quantity quantity) {
        const auto found = orders_.find(id);
        if (found == orders_.end()) return false;
        if (quantity <= 0) {
            cancel(id);
            return true;
        }
        found->second->quantity = quantity;
        found->second->remaining = quantity;
        return true;
    }

    void clear() {
        orders_.clear();
        bids_.clear();
        asks_.clear();
        accounts_.clear();
        trades_.clear();
        next_id_ = 1;
    }

    Quantity cancel(OrderId id) {
        const auto found = orders_.find(id);
        if (found == orders_.end()) return 0;
        Order& order = *found->second;
        Quantity cancelled = order.remaining;
        if (order.side == Side::Buy) {
            auto level = bids_.find(order.price);
            level->second.erase(order.queue_position);
            if (level->second.empty()) bids_.erase(level);
        } else {
            auto level = asks_.find(order.price);
            level->second.erase(order.queue_position);
            if (level->second.empty()) asks_.erase(level);
        }
        orders_.erase(found);
        return cancelled;
    }

    std::optional<Price> best_bid() const {
        if (bids_.empty()) return std::nullopt;
        return bids_.begin()->first;
    }
    std::optional<Price> best_ask() const {
        if (asks_.empty()) return std::nullopt;
        return asks_.begin()->first;
    }
    std::optional<Price> spread() const {
        if (bids_.empty() || asks_.empty()) return std::nullopt;
        return asks_.begin()->first - bids_.begin()->first;
    }
    std::optional<double> midpoint() const {
        if (bids_.empty() || asks_.empty()) return std::nullopt;
        return (static_cast<double>(asks_.begin()->first) + static_cast<double>(bids_.begin()->first)) / 2.0;
    }

    Quantity depth(Side side, std::optional<std::size_t> levels = std::nullopt) const {
        Quantity total = 0;
        std::size_t count = 0;
        if (side == Side::Buy) {
            for (const auto& [price, queue] : bids_) {
                (void)price;
                if (levels && count >= *levels) break;
                for (const Order* order : queue) total += order->remaining;
                ++count;
            }
        } else {
            for (const auto& [price, queue] : asks_) {
                (void)price;
                if (levels && count >= *levels) break;
                for (const Order* order : queue) total += order->remaining;
                ++count;
            }
        }
        return total;
    }

    std::vector<Level> snapshot(Side side, std::size_t levels = 5) const {
        std::vector<Level> result;
        if (side == Side::Buy) {
            for (const auto& [price, queue] : bids_) {
                if (result.size() == levels) break;
                Quantity quantity = 0;
                for (const Order* order : queue) quantity += order->remaining;
                result.push_back({price, quantity});
            }
        } else {
            for (const auto& [price, queue] : asks_) {
                if (result.size() == levels) break;
                Quantity quantity = 0;
                for (const Order* order : queue) quantity += order->remaining;
                result.push_back({price, quantity});
            }
        }
        return result;
    }

    const std::vector<Trade>& trades() const { return trades_; }
    const std::map<std::string, Account>& accounts() const { return accounts_; }
    const std::unordered_map<OrderId, std::unique_ptr<Order>>& live_orders() const { return orders_; }

private:
    using BidLevels = std::map<Price, std::list<Order*>, std::greater<Price>>;
    using AskLevels = std::map<Price, std::list<Order*>>;

    OrderResult submit(const std::string& trader, Side side, Quantity quantity, std::optional<Price> limit) {
        if (quantity <= 0) throw std::invalid_argument("quantity must be positive");
        if (limit && *limit <= 0) throw std::invalid_argument("price must be positive");
        if (next_id_ == 0) throw std::overflow_error("order ID space exhausted");
        const OrderId id = next_id_++;
        Quantity remaining = quantity;

        while (remaining > 0 && (side == Side::Buy ? !asks_.empty() : !bids_.empty())) {
            Price resting_price = side == Side::Buy ? asks_.begin()->first : bids_.begin()->first;
            const bool crosses = !limit || (side == Side::Buy ? resting_price <= *limit : resting_price >= *limit);
            if (!crosses) break;

            std::list<Order*>& queue = side == Side::Buy ? asks_.begin()->second : bids_.begin()->second;
            Order* resting = queue.front();
            const Quantity filled = std::min(remaining, resting->remaining);
            remaining -= filled;
            resting->remaining -= filled;

            const std::string buyer = side == Side::Buy ? trader : resting->trader;
            const std::string seller = side == Side::Buy ? resting->trader : trader;
            const OrderId buy_id = side == Side::Buy ? id : resting->id;
            const OrderId sell_id = side == Side::Buy ? resting->id : id;
            accounts_[buyer].apply_fill(Side::Buy, resting_price, filled);
            accounts_[seller].apply_fill(Side::Sell, resting_price, filled);
            trades_.push_back({resting_price, filled, buyer, seller, buy_id, sell_id});

            if (resting->remaining == 0) {
                queue.pop_front();
                orders_.erase(resting->id);
            }
            if (queue.empty()) {
                if (side == Side::Buy) asks_.erase(resting_price);
                else bids_.erase(resting_price);
            }
        }

        if (remaining > 0 && limit) {
            auto order = std::make_unique<Order>();
            order->id = id;
            order->trader = trader;
            order->side = side;
            order->price = *limit;
            order->quantity = quantity;
            order->remaining = remaining;
            Order* raw = order.get();
            if (side == Side::Buy) {
                auto& queue = bids_[*limit];
                queue.push_back(raw);
                raw->queue_position = std::prev(queue.end());
            } else {
                auto& queue = asks_[*limit];
                queue.push_back(raw);
                raw->queue_position = std::prev(queue.end());
            }
            orders_.emplace(id, std::move(order));
        }
        return {id, remaining};
    }

    BidLevels bids_;
    AskLevels asks_;
    std::unordered_map<OrderId, std::unique_ptr<Order>> orders_;
    std::map<std::string, Account> accounts_;
    std::vector<Trade> trades_;
    OrderId next_id_ = 1;
};

}  // namespace lob
