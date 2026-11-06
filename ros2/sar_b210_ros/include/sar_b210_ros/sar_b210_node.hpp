#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_srvs/srv/trigger.hpp>

#include "sar/capture_session.hpp"
#include "sar/run_config.hpp"
#include "sar/run_log.hpp"
#include "sar_b210_msgs/msg/sar_burst.hpp"
#include "sar_b210_msgs/msg/sar_clock_sync.hpp"
#include "sar_b210_msgs/msg/sar_status.hpp"
#include "sar_b210_msgs/srv/start.hpp"
#include "sar_b210_ros/spsc_ring.hpp"

namespace sar_b210_ros {

using StatusMsg = sar_b210_msgs::msg::SarStatus;

struct StartResult {
    bool accepted = false;
    std::string message;
    std::string run_dir;
};

// Wraps one CaptureSession per Start call. Everything slow (opening the radio, preflight, writing
// the run folder) runs on a worker thread. The burst callback runs on the capture control thread
// and only copies into a preallocated lock-free ring; a publisher thread drains it, so a slow
// subscriber costs queue drops (counted in SarStatus), never burst timing.
class SarB210Node : public rclcpp::Node {
public:
    using RadioFactory = std::function<std::unique_ptr<sar::Radio>(
        const sar::RunConfig&, std::shared_ptr<sar::RunLog>, bool transmit)>;

    explicit SarB210Node(const rclcpp::NodeOptions& options = rclcpp::NodeOptions(),
                         RadioFactory radio_factory = nullptr);
    ~SarB210Node() override;

    // The Start service body, callable without a ROS service round trip (tests). Refusals happen
    // here, before any radio is opened: busy, TX without the token, config/preflight errors.
    StartResult handle_start(bool transmit, const std::string& confirm, double duration_s);
    StartResult handle_stop();
    uint8_t state() const { return state_.load(); }
    StatusMsg make_status() const;
    // Blocks until the worker thread has finished (tests, shutdown).
    void join_worker();

private:
    struct BurstSlot {
        sar::BurstRecord rec;
        int64_t stamp_ns = 0;
        std::size_t num_samples = 0;
        std::vector<int16_t> iq;  // interleaved I/Q, capacity reserved up front
    };
    // Counters and queues of one run; shared with the callbacks by raw pointer (outlives the session).
    struct Run {
        Run(std::size_t depth, std::size_t slot_int16s, double master_clock_hz, int64_t ticks_per_sample, bool iq);
        SpscRing<BurstSlot> bursts;
        SpscRing<sar::ClockSyncRecord> syncs;
        const double master_clock_hz;
        const int64_t ticks_per_sample;
        const bool publish_iq;
        // control thread only (burst and clock-sync callbacks run on the same thread):
        sar::ClockSyncRecord latest_sync{};
        bool have_sync = false;
        std::atomic<uint64_t> drops{0};
        std::atomic<uint64_t> sync_drops{0};
        std::atomic<uint64_t> published{0};
        std::atomic<int64_t> round_trip_ns{0};
        std::atomic<int64_t> max_round_trip_ns{0};
        std::atomic<bool> producers_done{false};
    };
    struct Snapshot {
        sar::CaptureStatus cap;
        int64_t round_trip_ns = 0, max_round_trip_ns = 0;
        uint32_t queue_capacity = 0, queue_fill = 0, queue_max_fill = 0;
        uint64_t published = 0, drops = 0;
        double budget_sweep_s = 0, budget_step_s = 0;
        bool have = false;
    };

    void worker_main(sar::RunConfig config, bool transmit, std::string run_dir);
    void publisher_main(Run* run);
    void publish_status();
    void set_state(uint8_t s);
    Snapshot snapshot_locked() const;
    std::string resolve_run_dir(bool transmit) const;

    RadioFactory radio_factory_;
    std::string config_path_;
    std::string run_root_;
    bool write_run_folder_ = true;
    bool publish_iq_ = true;
    std::size_t queue_depth_ = 192;

    rclcpp::Publisher<sar_b210_msgs::msg::SarBurst>::SharedPtr burst_pub_;
    rclcpp::Publisher<StatusMsg>::SharedPtr status_pub_;
    rclcpp::Publisher<sar_b210_msgs::msg::SarClockSync>::SharedPtr sync_pub_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr config_pub_;
    rclcpp::Service<sar_b210_msgs::srv::Start>::SharedPtr start_srv_;
    rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr stop_srv_;
    rclcpp::TimerBase::SharedPtr status_timer_;

    std::atomic<uint8_t> state_{StatusMsg::STATE_IDLE};
    std::atomic<bool> stop_requested_{false};
    mutable std::mutex mu_;  // guards everything below
    std::thread worker_;
    std::shared_ptr<sar::CaptureSession> session_;
    std::unique_ptr<Run> run_;
    Snapshot last_;
    std::string run_dir_;
    std::string fatal_error_;
    bool transmit_ = false;
    double max_run_s_ = 0;
    double budget_sweep_s_ = 0, budget_step_s_ = 0;
};

}  // namespace sar_b210_ros
