#include "sar/ram_buffer.hpp"

#include <sys/mman.h>

#include <stdexcept>
#include <string>

namespace sar {

RamBuffer::RamBuffer(std::size_t num_slots, std::size_t samples_per_slot)
    : num_slots_(num_slots),
      samples_per_slot_(samples_per_slot),
      bytes_(num_slots * samples_per_slot * sizeof(std::complex<int16_t>)) {
    void* memory = ::mmap(nullptr, bytes_, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    if (memory == MAP_FAILED) {
        throw std::runtime_error("cannot allocate " + std::to_string(bytes_) + " bytes for the RAM buffer");
    }
    data_ = static_cast<std::complex<int16_t>*>(memory);
    locked_ = ::mlock(memory, bytes_) == 0;
}

RamBuffer::~RamBuffer() { ::munmap(data_, bytes_); }

}  // namespace sar
