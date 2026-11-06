#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include "sar/burst_record.hpp"
#include "sar/budget.hpp"
#include "sar/chirp.hpp"
#include "sar/radio.hpp"
#include "sar/ram_buffer.hpp"
#include "sar/run_config.hpp"
#include "sar/run_log.hpp"

namespace sar {

struct CaptureOptions {
    bool transmit = true;
};

struct CaptureStatus {
    bool running;
    uint64_t bursts;
    uint64_t invalid_bursts;
};

// One capture run: embeddable, with no dependence on main() or signal handling.
//
//   auto log = std::make_shared<sar::RunLog>();
//   sar::CaptureSession session(config, sar::make_uhd_radio(config, log, true), log, {});
//   session.start();
//   ...                       // session.status() is safe to poll from any thread
//   session.request_stop();
//   session.wait();
//   session.write_dataset(run_dir);
class CaptureSession {
public:
    // Validates the config and allocates RAM. Throws if the config has validation errors.
    CaptureSession(RunConfig config, std::unique_ptr<Radio> radio, std::shared_ptr<RunLog> log,
                   CaptureOptions options);
    ~CaptureSession();
    CaptureSession(const CaptureSession&) = delete;
    CaptureSession& operator=(const CaptureSession&) = delete;

    // Checks every step tunes, locks and stays in the permitted band, then starts the threads.
    // Throws before anything is transmitted if a check fails.
    void start();

    // The run also ends by itself at timing.max_run_s or on a fatal radio error.
    void request_stop();
    void wait();
    CaptureStatus status() const;

    // Set after wait() if the radio failed mid-run. Bursts captured so far can still be written.
    const std::optional<std::string>& fatal_error() const { return fatal_error_; }

    // Writes the run folder after wait(). Throws if it exists or the disk is too small, leaving
    // the data in RAM so another location can be tried.
    void write_dataset(const std::string& run_dir) const;

    const std::vector<BurstRecord>& bursts() const { return bursts_; }
    const std::vector<ClockSyncRecord>& clock_sync() const { return clock_sync_; }
    const Budget& budget() const { return budget_; }

private:
    void control_loop();
    void record_clock_sync();
    bool wait_for_lock();

    RunConfig config_;
    std::unique_ptr<Radio> radio_;
    std::shared_ptr<RunLog> log_;
    CaptureOptions options_;
    Budget budget_;
    std::vector<Sample> chirp_;
    std::vector<Sample> tx_burst_;
    RamBuffer iq_;
    std::vector<BurstRecord> bursts_;
    std::vector<ClockSyncRecord> clock_sync_;
    std::optional<std::string> fatal_error_;
    std::thread control_thread_;
    std::atomic<bool> stop_{false};
    std::atomic<bool> running_{false};
    std::atomic<uint64_t> burst_count_{0};
    std::atomic<uint64_t> invalid_count_{0};
};

}  // namespace sar
