#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
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

// Live callbacks, for consumers that want each burst while the run is going (e.g. a ROS node).
//
// Both are called on the session's control thread, the thread that times the bursts:
//
//   * BurstCallback gets every burst exactly once, in index order, after its record is final (the
//     same values bursts() will hold), including invalid bursts. `iq` points at the burst's
//     num_samples samples in the RAM buffer. Both references are valid ONLY for the duration of
//     the call: copy what you need before returning. (Today the slot is not reused until the
//     session is destroyed, but a ring buffer may replace it; do not rely on that.) A consumer
//     that only wants metadata can ignore `iq`, which costs nothing.
//   * ClockSyncCallback gets every clock_sync() row once, in order, right after it is taken
//     (the first before any burst, the last after the final burst).
//
// Timing: burst N is delivered while burst N+1 is already commanded and in flight, so a callback
// shorter than one burst adds nothing to the burst period; a longer one delays the next retune
// (burst spacing grows) but never a command already issued, so it cannot by itself make a burst
// late. Clock-sync calls run between bursts (once per timing.clock_sync_interval_s), so their
// time does add to that gap. A periodic clock-sync row taken after burst N may arrive before
// burst N's call; a consumer that stamps bursts from the latest sync row just gets a fresher one.
//
// Contract (the burst timing depends on it): a callback must not block, wait on I/O, take a lock
// another thread can hold for long, or allocate heavily. Copy into a preallocated or bounded
// queue and return; publish from another thread. Exceptions are caught and counted
// (CaptureStatus::callback_errors, the first one is logged) and never end the run. The slowest
// call is reported as CaptureStatus::max_callback_s. Unset callbacks cost one branch per burst.
using BurstCallback = std::function<void(const BurstRecord& record, const Sample* iq, std::size_t num_samples)>;
using ClockSyncCallback = std::function<void(const ClockSyncRecord& sync)>;

struct CaptureOptions {
    bool transmit = true;
    BurstCallback on_burst;          // optional
    ClockSyncCallback on_clock_sync;  // optional
};

// A cheap snapshot for polling from any thread. Each field is read atomically on its own; the
// set is not one consistent instant. Times are USRP time (master clock ticks / rate).
struct CaptureStatus {
    bool running = false;
    bool failed = false;              // a fatal radio error ended the run (detail: fatal_error())
    uint64_t bursts = 0;              // also the RAM slots used
    uint64_t invalid_bursts = 0;
    uint64_t ram_slots = 0;           // RAM buffer capacity; the run ends when bursts reaches it
    double elapsed_s = 0;             // run start to the latest burst
    uint64_t sweeps = 0;              // sweep periods measured (step-0 burst to step-0 burst)
    double last_sweep_period_s = 0;
    double mean_sweep_period_s = 0;
    double max_sweep_period_s = 0;
    double max_burst_gap_s = 0;       // largest start-to-start spacing of consecutive bursts
    uint32_t last_error_flags = 0;    // flags of the latest invalid burst (describe_burst_errors)
    uint64_t callback_errors = 0;     // callbacks that threw
    double max_callback_s = 0;        // slowest burst or clock-sync callback
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

    // Read these after wait(); during a run use status() or the callbacks.
    const std::vector<BurstRecord>& bursts() const { return bursts_; }
    const std::vector<ClockSyncRecord>& clock_sync() const { return clock_sync_; }
    const Budget& budget() const { return budget_; }

private:
    void control_loop();
    void record_clock_sync();
    bool wait_for_lock();
    void deliver_burst(std::size_t index) noexcept;
    void deliver_clock_sync(const ClockSyncRecord& sync) noexcept;
    void callback_finished(int64_t started_ns, const char* failure) noexcept;

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
    // Written only by the control thread (relaxed); read by status().
    std::atomic<bool> failed_{false};
    std::atomic<int64_t> elapsed_ticks_{0};
    std::atomic<uint64_t> sweep_count_{0};
    std::atomic<int64_t> last_sweep_ticks_{0};
    std::atomic<int64_t> mean_sweep_ticks_{0};
    std::atomic<int64_t> max_sweep_ticks_{0};
    std::atomic<int64_t> max_gap_ticks_{0};
    std::atomic<uint32_t> last_error_flags_{0};
    std::atomic<uint64_t> callback_errors_{0};
    std::atomic<int64_t> max_callback_ns_{0};
};

}  // namespace sar
