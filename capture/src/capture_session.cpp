#include "sar/capture_session.hpp"

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <ctime>
#include <exception>
#include <functional>
#include <mutex>
#include <sstream>
#include <stdexcept>

#include "sar/dataset_writer.hpp"

namespace sar {
namespace {

std::string join_errors(const std::vector<std::string>& errors) {
    std::string joined = "invalid config:";
    for (const std::string& error : errors) {
        joined += "\n  " + error;
    }
    return joined;
}

const RunConfig& checked(const RunConfig& config) {
    const ConfigReport report = validate_config(config);
    if (!report.errors.empty()) {
        throw std::runtime_error(join_errors(report.errors));
    }
    return config;
}

int64_t clock_ns(clockid_t clock) {
    timespec now{};
    clock_gettime(clock, &now);
    return static_cast<int64_t>(now.tv_sec) * 1000000000 + now.tv_nsec;
}

struct BurstJob {
    Sample* samples;
    int64_t tx_tick;
    int64_t rx_tick;
};

// A streaming thread that runs one burst at a time, handed over by the control thread.
class BurstWorker {
public:
    using Run = std::function<uint32_t(const BurstJob&)>;

    BurstWorker(Radio& radio, Run run) : run_(std::move(run)), thread_([this, &radio] { loop(radio); }) {}

    ~BurstWorker() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            exit_ = true;
        }
        changed_.notify_all();
        thread_.join();
    }

    void post(const BurstJob& job) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            job_ = job;
            has_job_ = true;
            done_ = false;
        }
        changed_.notify_all();
    }

    uint32_t wait() {
        std::unique_lock<std::mutex> lock(mutex_);
        changed_.wait(lock, [this] { return done_; });
        return flags_;
    }

    const std::string& error() const { return error_; }

private:
    void loop(Radio& radio) {
        radio.prepare_streaming_thread();
        std::unique_lock<std::mutex> lock(mutex_);
        while (true) {
            changed_.wait(lock, [this] { return has_job_ || exit_; });
            if (exit_) {
                return;
            }
            const BurstJob job = job_;
            has_job_ = false;
            lock.unlock();

            uint32_t flags = kBurstRadioError;
            std::string error;
            try {
                flags = run_(job);
            } catch (const std::exception& caught) {
                error = caught.what();
            }

            lock.lock();
            flags_ = flags;
            if (!error.empty()) {
                error_ = error;
            }
            done_ = true;
            changed_.notify_all();
        }
    }

    Run run_;
    std::mutex mutex_;
    std::condition_variable changed_;
    BurstJob job_{};
    bool has_job_ = false;
    bool done_ = false;
    bool exit_ = false;
    uint32_t flags_ = kBurstOk;
    std::string error_;
    std::thread thread_;  // last, so everything above exists when it starts
};

}  // namespace

std::string describe_burst_errors(uint32_t flags) {
    static const std::pair<uint32_t, const char*> kNames[] = {
        {kBurstOverflow, "rx_overflow"},       {kBurstSequenceError, "rx_sequence"},
        {kBurstTimeout, "rx_timeout"},         {kBurstLateCommand, "rx_late"},
        {kBurstLoNotLocked, "lo_not_locked"},  {kBurstTxUnderflow, "tx_underflow"},
        {kBurstTxLate, "tx_late"},             {kBurstTxSequenceError, "tx_sequence"},
        {kBurstTxNoAck, "tx_no_ack"},          {kBurstTxShort, "tx_short"},
        {kBurstRxBadPacket, "rx_bad_packet"},  {kBurstRxTimeMismatch, "rx_time_mismatch"},
        {kBurstRadioError, "radio_error"},
    };
    std::string names;
    for (const auto& [bit, name] : kNames) {
        if (flags & bit) {
            names += (names.empty() ? "" : " ") + std::string(name);
        }
    }
    return names;
}

CaptureSession::CaptureSession(RunConfig config, std::unique_ptr<Radio> radio, std::shared_ptr<RunLog> log,
                               CaptureOptions options)
    : config_(checked(config)),
      radio_(std::move(radio)),
      log_(std::move(log)),
      options_(options),
      budget_(compute_budget(config_)),
      chirp_(generate_chirp(config_)),
      tx_burst_(make_tx_burst(config_, chirp_)),
      iq_(budget_.ram_slots, burst_samples(config_)) {
    bursts_.reserve(iq_.num_slots());
    clock_sync_.reserve(static_cast<std::size_t>(config_.timing.max_run_s / config_.timing.clock_sync_interval_s) + 8);

    log_->add(format_budget(config_, budget_));
    for (const std::string& warning : validate_config(config_).warnings) {
        log_->add("WARNING: " + warning);
    }
    if (!iq_.locked()) {
        log_->add("WARNING: RAM buffer is not page-locked; check the memlock limit");
    }
    if (!options_.transmit) {
        log_->add("RX ONLY: nothing is transmitted, so this run has no leakage or target returns");
    }
}

CaptureSession::~CaptureSession() {
    request_stop();
    if (control_thread_.joinable()) {
        control_thread_.join();
    }
}

void CaptureSession::start() {
    if (control_thread_.joinable() || !bursts_.empty()) {
        throw std::logic_error("a CaptureSession runs once");
    }
    const double half_bandwidth = config_.waveform.chirp_bandwidth_hz / 2;
    for (int step = 0; step < config_.waveform.num_steps; ++step) {
        const Radio::Tuned tuned = radio_->tune(step_center_freq_hz(config_, step), config_.rf.lo_offset_hz);
        if (tuned.tx_freq_hz - half_bandwidth < *config_.permitted_band.min_hz ||
            tuned.tx_freq_hz + half_bandwidth > *config_.permitted_band.max_hz) {
            throw std::runtime_error("step " + std::to_string(step) + " tuned outside permitted_band");
        }
        if (!wait_for_lock()) {
            throw std::runtime_error("LO did not lock at step " + std::to_string(step));
        }
    }
    running_ = true;
    control_thread_ = std::thread([this] { control_loop(); });
}

void CaptureSession::request_stop() { stop_ = true; }

void CaptureSession::wait() {
    if (control_thread_.joinable()) {
        control_thread_.join();
    }
}

CaptureStatus CaptureSession::status() const { return {running_.load(), burst_count_.load(), invalid_count_.load()}; }

bool CaptureSession::wait_for_lock() {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(config_.timing.lock_timeout_s);
    while (!radio_->lo_locked()) {
        if (std::chrono::steady_clock::now() > deadline) {
            return false;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    return true;
}

void CaptureSession::record_clock_sync() {
    const int64_t before = clock_ns(CLOCK_MONOTONIC);
    const int64_t ticks = radio_->time_now_ticks();
    const int64_t after = clock_ns(CLOCK_MONOTONIC);
    const int64_t realtime = clock_ns(CLOCK_REALTIME);
    // The realtime read came just after the query, so move it back to the same midpoint.
    const int64_t midpoint = before + (after - before) / 2;
    clock_sync_.push_back({ticks, midpoint, realtime - (clock_ns(CLOCK_MONOTONIC) - midpoint), after - before});
}

void CaptureSession::control_loop() {
    const int64_t chirp_ticks = static_cast<int64_t>(chirp_samples(config_)) * ticks_per_sample(config_);
    const int64_t wait_ticks =
        seconds_to_ticks(config_, std::max(config_.timing.settle_margin_s,
                                           config_.timing.command_lead_s + config_.waveform.chirp_length_s));
    const int64_t max_run_ticks = seconds_to_ticks(config_, config_.timing.max_run_s);
    const int64_t sync_ticks = seconds_to_ticks(config_, config_.timing.clock_sync_interval_s);
    const std::size_t samples = burst_samples(config_);

    try {
        int64_t first_tick = -1;  // set by the RX worker, read after rx.wait()
        BurstWorker rx(*radio_, [this, samples, &first_tick](const BurstJob& job) {
            const Radio::Received received = radio_->receive_burst(job.samples, samples, job.rx_tick);
            first_tick = received.first_tick;
            uint32_t flags = received.error_flags;
            if (received.num_samples != samples && flags == kBurstOk) {
                flags |= kBurstTimeout;  // short with no reported cause
            }
            if (received.first_tick >= 0 &&
                received.first_tick != job.rx_tick + config_.device.rx_timestamp_offset_ticks) {
                flags |= kBurstRxTimeMismatch;
            }
            return flags;
        });
        std::unique_ptr<BurstWorker> tx;
        if (options_.transmit) {
            tx = std::make_unique<BurstWorker>(*radio_, [this](const BurstJob& job) {
                return radio_->send_burst(tx_burst_.data(), tx_burst_.size(), job.tx_tick);
            });
        }

        record_clock_sync();
        const int64_t run_start = clock_sync_.back().usrp_ticks;
        int64_t next_sync = run_start + sync_ticks;

        for (uint32_t sweep = 0; !stop_; ++sweep) {
            for (int step = 0; step < config_.waveform.num_steps && !stop_; ++step) {
                const std::size_t index = bursts_.size();
                if (index == iq_.num_slots()) {
                    log_->add("RAM buffer full; ending the run");
                    stop_ = true;
                    break;
                }

                // Retune with no burst in flight, so TX never radiates while the LO moves.
                const Radio::Tuned tuned = radio_->tune(step_center_freq_hz(config_, step), config_.rf.lo_offset_hz);

                BurstRecord record;
                record.index = index;
                record.sweep = sweep;
                record.step = static_cast<uint32_t>(step);
                record.sample_offset = index * samples;
                record.num_samples = samples;
                record.tx_freq_hz = tuned.tx_freq_hz;
                record.rx_freq_hz = tuned.rx_freq_hz;
                record.lo_offset_hz = config_.rf.lo_offset_hz;
                record.tx_gain_db = radio_->tx_gain_db();
                record.rx_gain_db = radio_->rx_gain_db();

                const bool locked = wait_for_lock();
                const int64_t now = radio_->time_now_ticks();
                record.usrp_ticks = now;

                if (!locked) {
                    record.error_flags = kBurstLoNotLocked;  // an unlocked LO could radiate off frequency
                } else {
                    const int64_t tps = ticks_per_sample(config_);
                    const int64_t rx_tick = (now + wait_ticks + tps - 1) / tps * tps;
                    const BurstJob job{iq_.slot(index), rx_tick - chirp_ticks, rx_tick};
                    rx.post(job);
                    if (tx) {
                        tx->post(job);
                    }
                    record.error_flags = rx.wait();
                    if (tx) {
                        record.error_flags |= tx->wait();
                    }
                    record.usrp_ticks = first_tick >= 0 ? first_tick : rx_tick;
                    if (record.error_flags & kBurstRxTimeMismatch) {
                        log_->add("burst " + std::to_string(index) + " first sample at tick " + std::to_string(first_tick) +
                                  ", commanded " + std::to_string(rx_tick));
                    }
                }

                bursts_.push_back(record);
                ++burst_count_;
                if (record.error_flags != kBurstOk) {
                    ++invalid_count_;
                    log_->add("burst " + std::to_string(index) + " invalid: " + describe_burst_errors(record.error_flags));
                }
                if (record.error_flags & kBurstRadioError) {
                    const std::string detail = !rx.error().empty() ? rx.error() : (tx ? tx->error() : "");
                    throw std::runtime_error(detail);
                }

                if (record.usrp_ticks - run_start >= max_run_ticks) {
                    stop_ = true;
                } else if (record.usrp_ticks >= next_sync) {
                    record_clock_sync();
                    next_sync += sync_ticks;
                }
            }
        }
        record_clock_sync();
    } catch (const std::exception& error) {
        fatal_error_ = error.what();
        log_->add(std::string("FATAL: ") + error.what());
    }
    running_ = false;
}

void CaptureSession::write_dataset(const std::string& run_dir) const {
    if (running_) {
        throw std::logic_error("write_dataset while the run is active; call wait() first");
    }
    sar::write_dataset(run_dir, DatasetContents{config_, chirp_, iq_, bursts_, clock_sync_, *log_, options_.transmit,
                                                fatal_error_});
}

}  // namespace sar
