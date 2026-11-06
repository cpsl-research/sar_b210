#pragma once

#include <complex>
#include <cstddef>
#include <cstdint>

namespace sar {

// Preallocated, zeroed IQ store with one fixed slot per burst. Page-locked when the rlimit allows.
class RamBuffer {
public:
    RamBuffer(std::size_t num_slots, std::size_t samples_per_slot);
    ~RamBuffer();
    RamBuffer(const RamBuffer&) = delete;
    RamBuffer& operator=(const RamBuffer&) = delete;

    std::complex<int16_t>* slot(std::size_t index) { return data_ + index * samples_per_slot_; }
    const std::complex<int16_t>* slot(std::size_t index) const { return data_ + index * samples_per_slot_; }
    std::size_t num_slots() const { return num_slots_; }
    std::size_t samples_per_slot() const { return samples_per_slot_; }
    bool locked() const { return locked_; }

private:
    std::size_t num_slots_;
    std::size_t samples_per_slot_;
    std::size_t bytes_;
    std::complex<int16_t>* data_;
    bool locked_ = false;
};

}  // namespace sar
