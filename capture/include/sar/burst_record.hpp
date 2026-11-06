#pragma once

#include <cstdint>
#include <string>

namespace sar {

// Column order must match BURST_COLUMNS in src/b210_sar/dataset.py (checked by tests/test_layout.py).
inline constexpr const char* kBurstCsvHeader =
    "index,sweep,step,sample_offset,num_samples,usrp_ticks,usrp_time_s,tx_freq_hz,rx_freq_hz,lo_offset_hz,tx_gain_db,rx_gain_db,valid,error_flags";

enum BurstError : uint32_t {
    kBurstOk = 0,
    kBurstOverflow = 1u << 0,
    kBurstSequenceError = 1u << 1,
    kBurstTimeout = 1u << 2,
    kBurstLateCommand = 1u << 3,
    kBurstLoNotLocked = 1u << 4,  // nothing was transmitted or received for this burst
    kBurstTxUnderflow = 1u << 5,
    kBurstTxLate = 1u << 6,
    kBurstTxSequenceError = 1u << 7,
    kBurstTxNoAck = 1u << 8,
    kBurstTxShort = 1u << 9,
    kBurstRxBadPacket = 1u << 10,
    kBurstRxTimeMismatch = 1u << 11,  // first sample was not at the commanded tick
    kBurstRadioError = 1u << 12,      // the radio threw; the run stops after this burst
};

// Space-separated names of the flags set in error_flags.
std::string describe_burst_errors(uint32_t error_flags);

struct BurstRecord {
    uint64_t index = 0;
    uint32_t sweep = 0;
    uint32_t step = 0;
    uint64_t sample_offset = 0;  // in samples; every burst keeps its slot, so offsets never shift
    uint64_t num_samples = 0;    // missing samples of an invalid burst are zero
    int64_t usrp_ticks = 0;      // start of the first sample, in master clock ticks
    double tx_freq_hz = 0;
    double rx_freq_hz = 0;
    double lo_offset_hz = 0;
    double tx_gain_db = 0;
    double rx_gain_db = 0;
    uint32_t error_flags = kBurstOk;  // bad bursts are marked, never dropped
};

// One sample of USRP time against the host clocks, so the autonomy stack can place bursts on its
// own time base. Host times are the midpoint of the USRP time query; round_trip_ns bounds the error.
struct ClockSyncRecord {
    int64_t usrp_ticks = 0;
    int64_t host_monotonic_ns = 0;
    int64_t host_realtime_ns = 0;
    int64_t round_trip_ns = 0;
};

}  // namespace sar
