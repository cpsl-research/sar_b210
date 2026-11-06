#pragma once

#include <complex>
#include <cstdint>
#include <vector>

#include "sar/run_config.hpp"

namespace sar {

using Sample = std::complex<int16_t>;

// One period of the tapered linear FM chirp, centered at baseband. These integers are what is sent.
std::vector<Sample> generate_chirp(const RunConfig& config);

// The chirp as written to chirp_replica.cf32: the transmitted integers scaled to full scale = 1.
std::vector<std::complex<float>> chirp_to_replica(const std::vector<Sample>& chirp);

// dwell_chirps + 1 periods. TX leads RX by one chirp so latency never leaves an RX chirp window part empty.
std::vector<Sample> make_tx_burst(const RunConfig& config, const std::vector<Sample>& chirp);

}  // namespace sar
