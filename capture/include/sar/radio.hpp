#pragma once

#include <cstddef>
#include <cstdint>

#include "sar/chirp.hpp"

namespace sar {

// What capture needs from an SDR. Device-specific code stays behind this interface.
class Radio {
public:
    struct Tuned {
        double tx_freq_hz;
        double rx_freq_hz;
    };
    struct Received {
        uint32_t error_flags;
        std::size_t num_samples;  // fewer than requested only when error_flags is set
        int64_t first_tick;       // tick of the first sample, or -1 if none arrived
    };

    virtual ~Radio() = default;

    virtual Tuned tune(double freq_hz, double lo_offset_hz) = 0;
    virtual bool lo_locked() = 0;
    virtual int64_t time_now_ticks() = 0;

    // Sends a burst timed to start at the tick; blocks until the device acknowledges its end.
    // Returns BurstError flags. Called only from the TX thread, and never in an RX-only session.
    virtual uint32_t send_burst(const Sample* samples, std::size_t count, int64_t start_tick) = 0;

    // Receives count samples starting at the tick. Called only from the RX thread.
    virtual Received receive_burst(Sample* samples, std::size_t count, int64_t start_tick) = 0;

    virtual double tx_gain_db() = 0;
    virtual double rx_gain_db() = 0;

    // Called once at the start of each streaming thread.
    virtual void prepare_streaming_thread() {}
};

}  // namespace sar
