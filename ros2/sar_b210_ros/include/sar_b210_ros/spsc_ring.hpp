#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace sar_b210_ros {

// Fixed-capacity single-producer single-consumer queue of preallocated slots.
//
// The producer is the capture control thread (the timed loop), so push must never block, lock or
// allocate: try_acquire() hands out the next free slot or nullptr when the queue is full (the
// caller counts that as a drop), commit() publishes it. The consumer (publisher thread) peeks the
// oldest slot, reads it in place, then pops. Slots are constructed once, up front, by the factory.
template <typename Slot>
class SpscRing {
public:
    template <typename Factory>
    SpscRing(std::size_t capacity, Factory make_slot) : slots_(capacity) {
        for (Slot& s : slots_) {
            s = make_slot();
        }
    }

    std::size_t capacity() const { return slots_.size(); }

    // Producer side.
    Slot* try_acquire() {
        const uint64_t head = head_.load(std::memory_order_relaxed);
        if (head - tail_.load(std::memory_order_acquire) >= slots_.size()) {
            return nullptr;
        }
        return &slots_[head % slots_.size()];
    }
    void commit() {
        const uint64_t head = head_.load(std::memory_order_relaxed) + 1;
        head_.store(head, std::memory_order_release);
        const uint64_t fill = head - tail_.load(std::memory_order_relaxed);
        uint64_t prev = max_fill_.load(std::memory_order_relaxed);
        if (fill > prev) {
            max_fill_.store(fill, std::memory_order_relaxed);  // single writer
        }
    }

    // Consumer side.
    Slot* peek() {
        const uint64_t tail = tail_.load(std::memory_order_relaxed);
        if (head_.load(std::memory_order_acquire) == tail) {
            return nullptr;
        }
        return &slots_[tail % slots_.size()];
    }
    void pop() { tail_.store(tail_.load(std::memory_order_relaxed) + 1, std::memory_order_release); }

    // Any thread.
    std::size_t fill() const {
        const uint64_t tail = tail_.load(std::memory_order_acquire);
        const uint64_t head = head_.load(std::memory_order_acquire);
        return static_cast<std::size_t>(head - tail);
    }
    std::size_t max_fill() const { return static_cast<std::size_t>(max_fill_.load(std::memory_order_relaxed)); }

private:
    std::vector<Slot> slots_;
    alignas(64) std::atomic<uint64_t> head_{0};
    alignas(64) std::atomic<uint64_t> tail_{0};
    std::atomic<uint64_t> max_fill_{0};
};

}  // namespace sar_b210_ros
