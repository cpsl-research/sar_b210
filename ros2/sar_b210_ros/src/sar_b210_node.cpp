#include "sar_b210_ros/sar_b210_node.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <sstream>

#include "sar/budget.hpp"
#include "sar/burst_record.hpp"
#include "sar/uhd_radio.hpp"

namespace sar_b210_ros {

namespace fs = std::filesystem;
using sar_b210_msgs::msg::SarBurst;
using sar_b210_msgs::msg::SarClockSync;

namespace {

constexpr const char* kTransmitToken = "TRANSMIT";

const char* state_name(uint8_t s) {
    switch (s) {
        case StatusMsg::STATE_IDLE: return "idle";
        case StatusMsg::STATE_PREFLIGHT: return "preflight";
        case StatusMsg::STATE_RUNNING: return "running";
        case StatusMsg::STATE_WRITING: return "writing";
        case StatusMsg::STATE_DONE: return "done";
        case StatusMsg::STATE_FAILED: return "failed";
        case StatusMsg::STATE_WRITE_FAILED: return "write_failed";
        default: return "unknown";
    }
}

builtin_interfaces::msg::Time to_stamp(int64_t ns) {
    builtin_interfaces::msg::Time t;
    t.sec = static_cast<int32_t>(ns / 1000000000LL);
    t.nanosec = static_cast<uint32_t>(ns % 1000000000LL);
    return t;
}

int64_t realtime_ns() {
    timespec ts{};
    clock_gettime(CLOCK_REALTIME, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000000000LL + ts.tv_nsec;
}

std::string expand_home(const std::string& path) {
    if (!path.empty() && path[0] == '~' && (path.size() == 1 || path[1] == '/')) {
        if (const char* home = std::getenv("HOME")) {
            return std::string(home) + path.substr(1);
        }
    }
    return path;
}

// base, or base_2, base_3, ... : the first path that does not exist yet.
std::string unique_path(const fs::path& base) {
    fs::path dir = base;
    std::error_code ec;
    for (int n = 2; fs::exists(dir, ec); ++n) {
        dir = base.string() + "_" + std::to_string(n);
    }
    return dir.string();
}

std::string join_errors(const std::vector<std::string>& errors) {
    std::string out;
    for (const std::string& e : errors) {
        out += (out.empty() ? "" : "; ") + e;
    }
    return out;
}

}  // namespace

SarB210Node::Run::Run(std::size_t depth, std::size_t slot_int16s, double hz, int64_t tps, bool iq)
    : bursts(depth,
             [slot_int16s] {
                 BurstSlot s;
                 s.iq.assign(slot_int16s, 0);  // allocated here, never in the callback
                 return s;
             }),
      syncs(256, [] { return sar::ClockSyncRecord{}; }),
      master_clock_hz(hz),
      ticks_per_sample(tps),
      publish_iq(iq) {}

void SarB210Node::apply_device_serial(sar::RunConfig& config, const std::string& serial) {
    if (serial.empty()) return;
    // Drop any serial= already in device.args (comma-separated UHD args) so it is never duplicated.
    std::string out;
    std::stringstream ss(config.device.args);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (item.empty() || item.rfind("serial=", 0) == 0) continue;
        if (!out.empty()) out += ',';
        out += item;
    }
    if (!out.empty()) out += ',';
    config.device.args = out + "serial=" + serial;
}

SarB210Node::SarB210Node(const rclcpp::NodeOptions& options, RadioFactory radio_factory)
    : rclcpp::Node("sar_b210", options), radio_factory_(std::move(radio_factory)) {
    if (!radio_factory_) {
        radio_factory_ = [](const sar::RunConfig& c, std::shared_ptr<sar::RunLog> log, bool tx) {
            return sar::make_uhd_radio(c, std::move(log), tx);
        };
    }
    config_path_ = declare_parameter<std::string>("config_path", "");
    run_root_ = expand_home(declare_parameter<std::string>("run_root", "b210_runs"));
    write_run_folder_ = declare_parameter<bool>("write_run_folder", true);
    device_serial_ = declare_parameter<std::string>("device_serial", "");
    publish_iq_ = declare_parameter<bool>("publish_iq", true);
    // Sizing: the measured rate is ~65 bursts/s (32.6 sweeps/s x 2 steps), 56,000 complex int16
    // samples (224 KB) per burst, ~14.6 MB/s. 192 slots hold ~2.9 s (~43 MB with IQ).
    queue_depth_ = static_cast<std::size_t>(std::max<int64_t>(4, declare_parameter<int64_t>("publish_queue_depth", 192)));

    burst_pub_ = create_publisher<SarBurst>("b210/bursts", rclcpp::QoS(rclcpp::KeepLast(50)).reliable());
    status_pub_ = create_publisher<StatusMsg>("b210/status", rclcpp::QoS(rclcpp::KeepLast(5)).reliable());
    sync_pub_ = create_publisher<SarClockSync>("b210/clock_sync", rclcpp::QoS(rclcpp::KeepLast(50)).reliable());
    config_pub_ = create_publisher<std_msgs::msg::String>("b210/config", rclcpp::QoS(1).reliable().transient_local());

    start_srv_ = create_service<sar_b210_msgs::srv::Start>(
        "b210/start", [this](const std::shared_ptr<sar_b210_msgs::srv::Start::Request> req,
                             std::shared_ptr<sar_b210_msgs::srv::Start::Response> res) {
            const StartResult r = handle_start(req->transmit, req->confirm, req->duration_s);
            res->accepted = r.accepted;
            res->message = r.message;
            res->run_dir = r.run_dir;
        });
    stop_srv_ = create_service<std_srvs::srv::Trigger>(
        "b210/stop", [this](const std::shared_ptr<std_srvs::srv::Trigger::Request>,
                            std::shared_ptr<std_srvs::srv::Trigger::Response> res) {
            const StartResult r = handle_stop();
            res->success = r.accepted;
            res->message = r.message;
        });
    write_retry_srv_ = create_service<sar_b210_msgs::srv::WriteRetry>(
        "b210/write_retry", [this](const std::shared_ptr<sar_b210_msgs::srv::WriteRetry::Request> req,
                                   std::shared_ptr<sar_b210_msgs::srv::WriteRetry::Response> res) {
            const StartResult r = handle_write_retry(req->path);
            res->accepted = r.accepted;
            res->message = r.message;
            res->run_dir = r.run_dir;
        });
    discard_srv_ = create_service<std_srvs::srv::Trigger>(
        "b210/discard", [this](const std::shared_ptr<std_srvs::srv::Trigger::Request>,
                               std::shared_ptr<std_srvs::srv::Trigger::Response> res) {
            const StartResult r = handle_discard();
            res->success = r.accepted;
            res->message = r.message;
        });
    status_timer_ = create_wall_timer(std::chrono::milliseconds(500), [this] { publish_status(); });

    // Latched config: the file's text now, the run's text again at each start.
    if (!config_path_.empty()) {
        try {
            std_msgs::msg::String msg;
            msg.data = sar::load_run_config(config_path_).source_text;
            config_pub_->publish(msg);
        } catch (const std::exception& e) {
            RCLCPP_WARN(get_logger(), "config_path '%s' not loadable: %s", config_path_.c_str(), e.what());
        }
    } else {
        RCLCPP_WARN(get_logger(), "config_path is empty: b210/start will refuse until it is set");
    }
    RCLCPP_INFO(get_logger(), "sar_b210 idle (RX-only by default; publish_iq=%s, queue %zu bursts, run_root %s)",
                publish_iq_ ? "true" : "false", queue_depth_, run_root_.c_str());
}

SarB210Node::~SarB210Node() {
    stop_requested_ = true;
    join_worker();
    if (held_) {
        RCLCPP_ERROR(get_logger(), "shutting down with an unwritten run held in RAM (target '%s'): that data is lost",
                     held_dir_.c_str());
    }
}

void SarB210Node::join_worker() {
    std::thread t;
    {
        std::lock_guard<std::mutex> lock(mu_);
        t = std::move(worker_);
    }
    if (t.joinable()) {
        t.join();
    }
}

void SarB210Node::set_state(uint8_t s) { state_.store(s); }

std::string SarB210Node::resolve_run_dir(bool transmit) const {
    if (!write_run_folder_) {
        return "";
    }
    char stamp[32];
    const std::time_t now = std::time(nullptr);
    std::tm tm{};
    localtime_r(&now, &tm);
    std::strftime(stamp, sizeof stamp, "%Y%m%d_%H%M%S", &tm);
    fs::path base = fs::path(run_root_) / (std::string("run_") + stamp + (transmit ? "_tx" : "_rx"));
    return fs::absolute(unique_path(base)).string();
}

StartResult SarB210Node::handle_start(bool transmit, const std::string& confirm, double duration_s) {
    StartResult out;
    if (transmit && confirm != kTransmitToken) {
        out.message = std::string("transmit: true refused: confirm must be exactly \"") + kTransmitToken +
                      "\". RX-only is the default. Nothing was opened or transmitted.";
        RCLCPP_WARN(get_logger(), "%s", out.message.c_str());
        return out;
    }
    if (duration_s < 0 || !std::isfinite(duration_s)) {
        out.message = "duration_s must be >= 0 (0 = the config's timing.max_run_s)";
        return out;
    }
    // Library preflight that needs no radio: same checks the CLI makes (band, max_tx_gain_db, ...).
    // Applies with the token too: the token is necessary, not sufficient.
    sar::RunConfig config;
    try {
        if (config_path_.empty()) {
            throw std::runtime_error("config_path parameter is not set");
        }
        config = sar::load_run_config(config_path_);
        apply_device_serial(config, device_serial_);
    } catch (const std::exception& e) {
        out.message = std::string("config not loadable: ") + e.what();
        return out;
    }
    if (duration_s > 0) {
        config.timing.max_run_s = duration_s;
    }
    const sar::ConfigReport report = sar::validate_config(config);
    if (!report.errors.empty()) {
        out.message = "config refused by preflight: " + join_errors(report.errors) + ". Nothing was opened.";
        RCLCPP_WARN(get_logger(), "%s", out.message.c_str());
        return out;
    }

    std::thread old;
    std::string run_dir;
    {
        std::lock_guard<std::mutex> lock(mu_);
        const uint8_t s = state_.load();
        if (held_) {
            out.message = "refused: the previous run's data is held in RAM because its run folder could not be written (" +
                          fatal_error_ + "). Call b210/write_retry (optionally with a path) or b210/discard first.";
            RCLCPP_WARN(get_logger(), "%s", out.message.c_str());
            return out;
        }
        if (s == StatusMsg::STATE_PREFLIGHT || s == StatusMsg::STATE_RUNNING || s == StatusMsg::STATE_WRITING) {
            out.message = std::string("a run is already in progress (state ") + state_name(s) + ")";
            return out;
        }
        old = std::move(worker_);
        run_dir = resolve_run_dir(transmit);
        run_dir_ = run_dir;
        fatal_error_.clear();
        transmit_ = transmit;
        max_run_s_ = config.timing.max_run_s;
        const sar::Budget budget = sar::compute_budget(config);
        budget_sweep_s_ = budget.sweep_time_s;
        budget_step_s_ = budget.step_time_s;
        last_ = Snapshot{};
        stop_requested_ = false;
        set_state(StatusMsg::STATE_PREFLIGHT);
    }
    if (old.joinable()) {
        old.join();
    }
    {
        std_msgs::msg::String msg;
        msg.data = config.source_text;
        config_pub_->publish(msg);
    }
    {
        std::lock_guard<std::mutex> lock(mu_);
        worker_ = std::thread([this, config, transmit, run_dir] { worker_main(config, transmit, run_dir); });
    }
    out.accepted = true;
    out.run_dir = run_dir;
    out.message = std::string(transmit ? "starting, TRANSMITTING" : "starting, RX-only (nothing is transmitted)") +
                  "; preflight and radio open run in the background, watch b210/status";
    RCLCPP_INFO(get_logger(), "%s (run_dir '%s', max_run_s %.1f)", out.message.c_str(), run_dir.c_str(),
                config.timing.max_run_s);
    return out;
}

StartResult SarB210Node::handle_stop() {
    StartResult out;
    const uint8_t s = state_.load();
    if (s == StatusMsg::STATE_PREFLIGHT || s == StatusMsg::STATE_RUNNING) {
        stop_requested_ = true;
        out.accepted = true;
        out.message = "stop requested";
    } else {
        out.message = std::string("no run to stop (state ") + state_name(s) + ")";
    }
    return out;
}

void SarB210Node::worker_main(sar::RunConfig config, bool transmit, std::string run_dir) {
    auto log = std::make_shared<sar::RunLog>();
    std::shared_ptr<sar::CaptureSession> session;
    Run* run = nullptr;
    std::thread publisher;
    std::string failure;
    auto stop_publisher = [&] {
        if (run) {
            run->producers_done.store(true, std::memory_order_release);
        }
        if (publisher.joinable()) {
            publisher.join();
        }
    };
    try {
        std::unique_ptr<sar::Radio> radio = radio_factory_(config, log, transmit);
        const std::size_t samples = sar::burst_samples(config);
        auto owned = std::make_unique<Run>(queue_depth_, publish_iq_ ? samples * 2 : 0,
                                           config.device.master_clock_rate_hz, sar::ticks_per_sample(config),
                                           publish_iq_);
        run = owned.get();

        sar::CaptureOptions options;
        options.transmit = transmit;
        // Runs on the capture control thread: no lock, no allocation, no blocking.
        options.on_clock_sync = [run](const sar::ClockSyncRecord& sync) {
            run->latest_sync = sync;
            run->have_sync = true;
            run->round_trip_ns.store(sync.round_trip_ns, std::memory_order_relaxed);
            if (sync.round_trip_ns > run->max_round_trip_ns.load(std::memory_order_relaxed)) {
                run->max_round_trip_ns.store(sync.round_trip_ns, std::memory_order_relaxed);
            }
            if (sar::ClockSyncRecord* slot = run->syncs.try_acquire()) {
                *slot = sync;
                run->syncs.commit();
            } else {
                run->sync_drops.fetch_add(1, std::memory_order_relaxed);
            }
        };
        options.on_burst = [run](const sar::BurstRecord& rec, const sar::Sample* iq, std::size_t n) {
            BurstSlot* slot = run->bursts.try_acquire();
            if (!slot) {
                run->drops.fetch_add(1, std::memory_order_relaxed);
                return;
            }
            slot->rec = rec;
            slot->num_samples = n;
            slot->stamp_ns = 0;
            if (run->have_sync) {
                const int64_t centre = rec.usrp_ticks + static_cast<int64_t>(n) * run->ticks_per_sample / 2;
                const double dt_s = static_cast<double>(centre - run->latest_sync.usrp_ticks) / run->master_clock_hz;
                slot->stamp_ns = run->latest_sync.host_realtime_ns + std::llround(dt_s * 1e9);
            }
            if (run->publish_iq) {
                const std::size_t count = std::min(n * 2, slot->iq.size());
                std::memcpy(slot->iq.data(), iq, count * sizeof(int16_t));
                slot->num_samples = count / 2;
            }
            run->bursts.commit();
        };

        session = std::make_shared<sar::CaptureSession>(config, std::move(radio), log, options);
        {
            std::lock_guard<std::mutex> lock(mu_);
            run_ = std::move(owned);
            session_ = session;
        }
        publisher = std::thread([this, run] { publisher_main(run); });

        if (stop_requested_) {
            throw std::runtime_error("stopped during preflight, nothing captured");
        }
        session->start();  // library preflight (every step tunes, locks, in band); throws before any TX
        set_state(StatusMsg::STATE_RUNNING);
        RCLCPP_INFO(get_logger(), "capture running");
        bool stop_sent = false;
        while (session->status().running) {
            if (stop_requested_ && !stop_sent) {
                session->request_stop();
                stop_sent = true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        session->wait();
        set_state(StatusMsg::STATE_WRITING);
        stop_publisher();  // drains the queue first
        if (session->fatal_error()) {
            failure = "run ended early: " + *session->fatal_error();
        }
        if (!run_dir.empty()) {
            const std::string write_error = write_session(*session, run_dir, true);
            settle_write(session, run_dir, failure, write_error);
            return;
        }
    } catch (const std::exception& e) {
        failure = e.what();
        stop_publisher();
    }
    if (!failure.empty()) {
        RCLCPP_ERROR(get_logger(), "%s", failure.c_str());
    }
    settle_write(session, run_dir, failure, "");
}

std::string SarB210Node::write_session(const sar::CaptureSession& session, std::string& dir, bool suffix_retry) {
    std::string error;
    try {
        session.write_dataset(dir);
        RCLCPP_INFO(get_logger(), "wrote %s", dir.c_str());
        return "";
    } catch (const std::exception& e) {
        error = e.what();
    }
    std::error_code ec;
    if (suffix_retry && fs::exists(dir, ec)) {
        const std::string alt = unique_path(dir);
        RCLCPP_WARN(get_logger(), "write to '%s' failed (%s); retrying once at '%s'", dir.c_str(), error.c_str(), alt.c_str());
        try {
            session.write_dataset(alt);
            RCLCPP_INFO(get_logger(), "wrote %s", alt.c_str());
            dir = alt;
            return "";
        } catch (const std::exception& e) {
            error += std::string("; retry at '") + alt + "' failed: " + e.what();
        }
    }
    return error;
}

void SarB210Node::settle_write(std::shared_ptr<sar::CaptureSession> session, const std::string& dir,
                               const std::string& run_failure, const std::string& write_error) {
    std::string failure = run_failure;
    if (!write_error.empty()) {
        failure += (failure.empty() ? "" : "; ") + std::string("run folder not written: ") + write_error;
        RCLCPP_ERROR(get_logger(), "%s. The data is held in RAM: call b210/write_retry or b210/discard.", failure.c_str());
    } else if (!failure.empty()) {
        RCLCPP_ERROR(get_logger(), "%s", failure.c_str());
    }
    {
        std::lock_guard<std::mutex> lock(mu_);
        if (!held_) {
            last_ = snapshot_locked();  // (a retry keeps the snapshot taken when the run ended)
        }
        fatal_error_ = failure;
        if (!dir.empty() && write_error.empty()) {
            run_dir_ = dir;
        }
        if (write_error.empty()) {
            held_.reset();
            held_dir_.clear();
            held_run_failure_.clear();
        } else {
            held_ = std::move(session);  // never free a session whose data has not been written
            held_dir_ = dir;
            held_run_failure_ = run_failure;
        }
        session_.reset();
        run_.reset();
    }
    set_state(!write_error.empty() ? StatusMsg::STATE_WRITE_FAILED
                                   : (failure.empty() ? StatusMsg::STATE_DONE : StatusMsg::STATE_FAILED));
}

StartResult SarB210Node::handle_write_retry(const std::string& path) {
    StartResult out;
    std::thread old;
    {
        std::lock_guard<std::mutex> lock(mu_);
        if (state_.load() != StatusMsg::STATE_WRITE_FAILED || !held_) {
            out.message = std::string("nothing to retry: no unwritten run is held (state ") + state_name(state_.load()) + ")";
            return out;
        }
        out.run_dir = path.empty() ? fs::absolute(unique_path(held_dir_)).string() : fs::absolute(expand_home(path)).string();
        old = std::move(worker_);
        set_state(StatusMsg::STATE_WRITING);
    }
    if (old.joinable()) {
        old.join();
    }
    {
        std::lock_guard<std::mutex> lock(mu_);
        run_dir_ = out.run_dir;
        const std::string dir = out.run_dir;
        worker_ = std::thread([this, dir] { retry_main(dir); });
    }
    out.accepted = true;
    out.message = "writing the held run to '" + out.run_dir + "' in the background, watch b210/status";
    RCLCPP_INFO(get_logger(), "%s", out.message.c_str());
    return out;
}

void SarB210Node::retry_main(std::string dir) {
    std::shared_ptr<sar::CaptureSession> session;
    std::string run_failure;
    {
        std::lock_guard<std::mutex> lock(mu_);
        session = held_;
        run_failure = held_run_failure_;
    }
    const std::string write_error = write_session(*session, dir, false);
    settle_write(std::move(session), dir, run_failure, write_error);
}

StartResult SarB210Node::handle_discard() {
    StartResult out;
    std::shared_ptr<sar::CaptureSession> freed;
    {
        std::lock_guard<std::mutex> lock(mu_);
        if (state_.load() != StatusMsg::STATE_WRITE_FAILED || !held_) {
            out.message = std::string("nothing to discard: no unwritten run is held (state ") + state_name(state_.load()) + ")";
            return out;
        }
        fatal_error_ += "; unwritten data discarded by operator";
        freed = std::move(held_);
        held_dir_.clear();
        held_run_failure_.clear();
        set_state(StatusMsg::STATE_FAILED);
    }
    freed.reset();
    out.accepted = true;
    out.message = "held run data discarded";
    RCLCPP_WARN(get_logger(), "%s", out.message.c_str());
    return out;
}

void SarB210Node::publisher_main(Run* run) {
    SarBurst msg;
    SarClockSync sync;
    while (true) {
        const bool producers_done = run->producers_done.load(std::memory_order_acquire);
        bool any = false;
        {
            while (BurstSlot* s = run->bursts.peek()) {
                msg.header.stamp = to_stamp(s->stamp_ns);
                msg.index = s->rec.index;
                msg.sweep = s->rec.sweep;
                msg.step = s->rec.step;
                msg.sample_offset = s->rec.sample_offset;
                msg.num_samples = s->rec.num_samples;
                msg.usrp_ticks = s->rec.usrp_ticks;
                msg.usrp_time_s = static_cast<double>(s->rec.usrp_ticks) / run->master_clock_hz;
                msg.tx_freq_hz = s->rec.tx_freq_hz;
                msg.rx_freq_hz = s->rec.rx_freq_hz;
                msg.lo_offset_hz = s->rec.lo_offset_hz;
                msg.tx_gain_db = s->rec.tx_gain_db;
                msg.rx_gain_db = s->rec.rx_gain_db;
                msg.valid = s->rec.error_flags == sar::kBurstOk;
                msg.error_flags = s->rec.error_flags;
                if (run->publish_iq) {
                    msg.iq.assign(s->iq.begin(), s->iq.begin() + static_cast<std::ptrdiff_t>(s->num_samples * 2));
                } else {
                    msg.iq.clear();
                }
                try {
                    burst_pub_->publish(msg);
                    run->published.fetch_add(1, std::memory_order_relaxed);
                } catch (const std::exception& e) {
                    RCLCPP_ERROR(get_logger(), "publish failed: %s", e.what());
                    run->drops.fetch_add(1, std::memory_order_relaxed);
                }
                run->bursts.pop();
                any = true;
            }
            while (sar::ClockSyncRecord* s = run->syncs.peek()) {
                sync.usrp_ticks = s->usrp_ticks;
                sync.host_monotonic_ns = s->host_monotonic_ns;
                sync.host_realtime_ns = s->host_realtime_ns;
                sync.round_trip_ns = s->round_trip_ns;
                try {
                    sync_pub_->publish(sync);
                } catch (const std::exception& e) {
                    RCLCPP_ERROR(get_logger(), "publish failed: %s", e.what());
                }
                run->syncs.pop();
                any = true;
            }
        }
        if (!any) {
            if (producers_done) {
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }
}

SarB210Node::Snapshot SarB210Node::snapshot_locked() const {
    if (!run_ || !session_) {
        return last_;
    }
    Snapshot s;
    s.cap = session_->status();
    s.round_trip_ns = run_->round_trip_ns.load(std::memory_order_relaxed);
    s.max_round_trip_ns = run_->max_round_trip_ns.load(std::memory_order_relaxed);
    s.queue_capacity = static_cast<uint32_t>(run_->bursts.capacity());
    s.queue_fill = static_cast<uint32_t>(run_->bursts.fill());
    s.queue_max_fill = static_cast<uint32_t>(run_->bursts.max_fill());
    s.published = run_->published.load(std::memory_order_relaxed);
    s.drops = run_->drops.load(std::memory_order_relaxed);
    s.have = true;
    return s;
}

StatusMsg SarB210Node::make_status() const {
    StatusMsg m;
    std::lock_guard<std::mutex> lock(mu_);
    const Snapshot s = snapshot_locked();
    m.header.stamp = to_stamp(realtime_ns());
    m.state = state_.load();
    m.state_name = state_name(m.state);
    m.transmit = transmit_;
    m.config_path = config_path_;
    m.run_dir = run_dir_;
    m.fatal_error = fatal_error_;
    m.bursts = s.cap.bursts;
    m.invalid_bursts = s.cap.invalid_bursts;
    m.ram_slots = s.cap.ram_slots;
    m.ram_fill = s.cap.ram_slots ? static_cast<float>(static_cast<double>(s.cap.bursts) / s.cap.ram_slots) : 0.f;
    m.elapsed_s = s.cap.elapsed_s;
    m.max_run_s = max_run_s_;
    m.burst_rate_hz = s.cap.elapsed_s > 0 ? static_cast<double>(s.cap.bursts) / s.cap.elapsed_s : 0.0;
    m.sweeps = s.cap.sweeps;
    m.budget_sweep_period_s = budget_sweep_s_;
    m.last_sweep_period_s = s.cap.last_sweep_period_s;
    m.mean_sweep_period_s = s.cap.mean_sweep_period_s;
    m.max_sweep_period_s = s.cap.max_sweep_period_s;
    m.budget_step_time_s = budget_step_s_;
    m.max_burst_gap_s = s.cap.max_burst_gap_s;
    m.last_error_flags = s.cap.last_error_flags;
    m.last_error_names = sar::describe_burst_errors(s.cap.last_error_flags);
    m.clock_round_trip_ns = s.round_trip_ns;
    m.max_clock_round_trip_ns = s.max_round_trip_ns;
    m.callback_errors = s.cap.callback_errors;
    m.max_callback_s = s.cap.max_callback_s;
    m.publish_iq = publish_iq_;
    m.publish_queue_capacity = s.queue_capacity ? s.queue_capacity : static_cast<uint32_t>(queue_depth_);
    m.publish_queue_fill = s.queue_fill;
    m.publish_queue_max_fill = s.queue_max_fill;
    m.published_bursts = s.published;
    m.publish_drops = s.drops;
    return m;
}

void SarB210Node::publish_status() { status_pub_->publish(make_status()); }

}  // namespace sar_b210_ros
