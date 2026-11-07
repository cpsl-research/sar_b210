// Radio-free tests: nothing here opens a radio. The radio factory is a counter that throws.
#include <gtest/gtest.h>

#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <thread>

#include <rclcpp/rclcpp.hpp>

#include "sar_b210_ros/sar_b210_node.hpp"
#include "sar_b210_ros/spsc_ring.hpp"

namespace {

std::atomic<int> g_radio_opens{0};

std::unique_ptr<sar::Radio> counting_factory(const sar::RunConfig&, std::shared_ptr<sar::RunLog>, bool) {
    ++g_radio_opens;
    throw std::runtime_error("test radio refuses to open");
}

std::string config_text(double tx_gain_db) {
    std::ifstream in(std::string(SAR_SOURCE_DIR) + "/configs/default.yaml");
    std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (tx_gain_db >= 0) {
        const std::string key = "tx_gain_db:";
        const auto at = text.find(key);
        const auto eol = text.find('\n', at);
        text.replace(at, eol - at, key + " " + std::to_string(tx_gain_db));
    }
    return text;
}

std::shared_ptr<sar_b210_ros::SarB210Node> make_node(const std::string& config_path) {
    rclcpp::NodeOptions opts;
    opts.append_parameter_override("config_path", config_path);
    opts.append_parameter_override("write_run_folder", false);
    return std::make_shared<sar_b210_ros::SarB210Node>(opts, counting_factory);
}

// Radio-free stand-in: the real clock, no device. Enough for CaptureSession to run RX-only.
class FakeRadio final : public sar::Radio {
public:
    explicit FakeRadio(double tick_rate) : tick_rate_(tick_rate), start_(std::chrono::steady_clock::now()) {}
    Tuned tune(double f, double) override { return {f, f}; }
    bool lo_locked() override { return true; }
    int64_t time_now_ticks() override {
        const std::chrono::duration<double> e = std::chrono::steady_clock::now() - start_;
        return std::llround(e.count() * tick_rate_);
    }
    uint32_t send_burst(const sar::Sample*, std::size_t, int64_t) override { return sar::kBurstOk; }
    Received receive_burst(sar::Sample* samples, std::size_t count, int64_t tick) override {
        std::this_thread::sleep_for(std::chrono::microseconds(200));
        for (std::size_t i = 0; i < count; ++i) samples[i] = sar::Sample(1, static_cast<int16_t>(i & 0x7fff));
        return {sar::kBurstOk, count, tick};
    }
    double tx_gain_db() override { return 20; }
    double rx_gain_db() override { return 30; }

private:
    double tick_rate_;
    std::chrono::steady_clock::time_point start_;
};

std::unique_ptr<sar::Radio> fake_factory(const sar::RunConfig& c, std::shared_ptr<sar::RunLog>, bool) {
    return std::make_unique<FakeRadio>(c.device.master_clock_rate_hz);
}

std::string set_key(std::string text, const std::string& key, const std::string& value) {
    const auto at = text.find(key + ":");
    const auto eol = text.find('\n', at);
    text.replace(at, eol - at, key + ": " + value);
    return text;
}

// A short RX-only run on the fake radio, written to run_root (which the test may make unwritable).
std::shared_ptr<sar_b210_ros::SarB210Node> make_fake_node(const std::string& base, const std::string& run_root) {
    std::string t = config_text(-1);
    for (const auto& kv : {std::pair<const char*, const char*>{"rx_timestamp_offset_ticks", "0"},
                           {"retune_s", "0"}, {"settle_margin_s", "0.001"}, {"command_lead_s", "0.0005"},
                           {"lock_timeout_s", "0.005"}, {"clock_sync_interval_s", "0.02"}, {"max_run_s", "0.2"}}) {
        t = set_key(t, kv.first, kv.second);
    }
    const std::string cfg = base + "/fast.yaml";
    std::ofstream(cfg) << t;
    rclcpp::NodeOptions opts;
    opts.append_parameter_override("config_path", cfg);
    opts.append_parameter_override("run_root", run_root);
    opts.append_parameter_override("publish_iq", false);
    return std::make_shared<sar_b210_ros::SarB210Node>(opts, fake_factory);
}

std::string fresh_dir(const std::string& name) {
    const std::string d = ::testing::TempDir() + "/" + name + "_" + std::to_string(::getpid());
    std::filesystem::remove_all(d);
    std::filesystem::create_directories(d);
    return d;
}

class NodeTest : public ::testing::Test {
protected:
    static void SetUpTestSuite() { rclcpp::init(0, nullptr); }
    static void TearDownTestSuite() { rclcpp::shutdown(); }
    void SetUp() override { g_radio_opens = 0; }
};

}  // namespace

TEST_F(NodeTest, TransmitWithoutTokenIsRefusedAndNothingOpens) {
    auto node = make_node(std::string(SAR_SOURCE_DIR) + "/configs/default.yaml");
    for (const char* confirm : {"", "transmit", "TRANSMIT ", "yes"}) {
        const auto r = node->handle_start(true, confirm, 0);
        EXPECT_FALSE(r.accepted) << confirm;
        EXPECT_NE(r.message.find("TRANSMIT"), std::string::npos);
        EXPECT_NE(r.message.find("Nothing was opened"), std::string::npos);
    }
    EXPECT_EQ(node->state(), sar_b210_msgs::msg::SarStatus::STATE_IDLE);
    EXPECT_EQ(g_radio_opens.load(), 0);
}

TEST_F(NodeTest, TokenDoesNotBypassPreflight) {
    const std::string path = ::testing::TempDir() + "/hot.yaml";
    std::ofstream(path) << config_text(80.0);  // far above rf.max_tx_gain_db
    auto node = make_node(path);
    const auto r = node->handle_start(true, "TRANSMIT", 0);
    EXPECT_FALSE(r.accepted);
    EXPECT_NE(r.message.find("max_tx_gain_db"), std::string::npos) << r.message;
    EXPECT_EQ(node->state(), sar_b210_msgs::msg::SarStatus::STATE_IDLE);
    EXPECT_EQ(g_radio_opens.load(), 0);
}

TEST_F(NodeTest, MissingConfigAndBadDurationAreRefused) {
    auto node = make_node("/nonexistent/none.yaml");
    EXPECT_FALSE(node->handle_start(false, "", 0).accepted);
    EXPECT_FALSE(node->handle_start(false, "", -1).accepted);
    EXPECT_EQ(g_radio_opens.load(), 0);
}

std::string g_last_args;

std::unique_ptr<sar::Radio> args_factory(const sar::RunConfig& c, std::shared_ptr<sar::RunLog>, bool) {
    g_last_args = c.device.args;
    throw std::runtime_error("test radio refuses to open");
}

TEST_F(NodeTest, DeviceSerialParamReachesRadioArgsWithoutDuplicating) {
    const std::string cfg = std::string(SAR_SOURCE_DIR) + "/configs/default.yaml";
    auto run = [&](const std::string& serial) {
        rclcpp::NodeOptions opts;
        opts.append_parameter_override("config_path", cfg);
        opts.append_parameter_override("write_run_folder", false);
        if (!serial.empty()) opts.append_parameter_override("device_serial", serial);
        auto node = std::make_shared<sar_b210_ros::SarB210Node>(opts, args_factory);
        EXPECT_TRUE(node->handle_start(false, "", 1.0).accepted);
        node->join_worker();
        return g_last_args;
    };
    const std::string base = run("");
    EXPECT_EQ(base.find("serial="), std::string::npos);
    EXPECT_EQ(run("3271117"), base + ",serial=3271117");
    sar::RunConfig c;
    c.device.args = "type=b200,serial=AAA";
    sar_b210_ros::SarB210Node::apply_device_serial(c, "BBB");
    EXPECT_EQ(c.device.args, "type=b200,serial=BBB");
    sar_b210_ros::SarB210Node::apply_device_serial(c, "BBB");
    EXPECT_EQ(c.device.args, "type=b200,serial=BBB");
    c.device.args = "type=b200";
    sar_b210_ros::SarB210Node::apply_device_serial(c, "");
    EXPECT_EQ(c.device.args, "type=b200");
}

TEST_F(NodeTest, RxOnlyStartFailsCleanlyWhenRadioCannotOpen) {
    auto node = make_node(std::string(SAR_SOURCE_DIR) + "/configs/default.yaml");
    const auto r = node->handle_start(false, "", 1.0);
    ASSERT_TRUE(r.accepted) << r.message;
    node->join_worker();
    EXPECT_EQ(g_radio_opens.load(), 1);
    EXPECT_EQ(node->state(), sar_b210_msgs::msg::SarStatus::STATE_FAILED);
    const auto status = node->make_status();
    EXPECT_NE(status.fatal_error.find("test radio"), std::string::npos);
    EXPECT_FALSE(status.transmit);
    EXPECT_EQ(status.publish_drops, 0u);
    // A failed run can be followed by another.
    EXPECT_TRUE(node->handle_start(false, "", 1.0).accepted);
    node->join_worker();
    EXPECT_EQ(g_radio_opens.load(), 2);
}

namespace fs = std::filesystem;
using sar_b210_msgs::msg::SarStatus;

TEST_F(NodeTest, WriteFailureKeepsDataRefusesStartRetryAndDiscard) {
    const std::string base = fresh_dir("wf");
    std::ofstream(base + "/blocker") << "a file, so no run folder can be created under it";
    auto node = make_fake_node(base, base + "/blocker/runs");
    const auto r = node->handle_start(false, "", 0);
    ASSERT_TRUE(r.accepted) << r.message;
    node->join_worker();
    ASSERT_EQ(node->state(), SarStatus::STATE_WRITE_FAILED) << node->make_status().fatal_error;
    auto st = node->make_status();
    EXPECT_EQ(st.state_name, "write_failed");
    EXPECT_NE(st.fatal_error.find("run folder not written"), std::string::npos) << st.fatal_error;
    EXPECT_GT(st.bursts, 10u);
    const uint64_t bursts = st.bursts;

    // Start is refused, with the reason, and nothing was opened or changed.
    const auto again = node->handle_start(false, "", 0);
    EXPECT_FALSE(again.accepted);
    EXPECT_NE(again.message.find("held in RAM"), std::string::npos) << again.message;
    EXPECT_EQ(node->state(), SarStatus::STATE_WRITE_FAILED);

    // Retry to an unwritable path keeps the data again.
    const auto bad = node->handle_write_retry(base + "/blocker/other");
    ASSERT_TRUE(bad.accepted) << bad.message;
    node->join_worker();
    EXPECT_EQ(node->state(), SarStatus::STATE_WRITE_FAILED);
    EXPECT_EQ(node->make_status().bursts, bursts);

    // Retry to a good path writes the same data and frees the hold.
    const std::string good = base + "/good_run";
    const auto ok = node->handle_write_retry(good);
    ASSERT_TRUE(ok.accepted) << ok.message;
    node->join_worker();
    EXPECT_EQ(node->state(), SarStatus::STATE_DONE) << node->make_status().fatal_error;
    st = node->make_status();
    EXPECT_EQ(st.run_dir, good);
    EXPECT_EQ(st.bursts, bursts);
    EXPECT_TRUE(fs::exists(good + "/iq.sc16"));
    EXPECT_TRUE(fs::exists(good + "/bursts.csv"));
    EXPECT_FALSE(node->handle_write_retry("").accepted);  // nothing held any more
    EXPECT_FALSE(node->handle_discard().accepted);
    EXPECT_TRUE(node->handle_start(false, "", 0).accepted);  // a new run is allowed again
    node->join_worker();
}

TEST_F(NodeTest, DiscardFreesHeldData) {
    const std::string base = fresh_dir("dc");
    std::ofstream(base + "/blocker") << "x";
    auto node = make_fake_node(base, base + "/blocker/runs");
    ASSERT_TRUE(node->handle_start(false, "", 0).accepted);
    node->join_worker();
    ASSERT_EQ(node->state(), SarStatus::STATE_WRITE_FAILED);
    const auto d = node->handle_discard();
    EXPECT_TRUE(d.accepted) << d.message;
    EXPECT_EQ(node->state(), SarStatus::STATE_FAILED);
    EXPECT_NE(node->make_status().fatal_error.find("discarded"), std::string::npos);
    EXPECT_FALSE(node->handle_write_retry("").accepted);
    ASSERT_TRUE(node->handle_start(false, "", 0).accepted);  // allowed after discard
    node->join_worker();
    EXPECT_EQ(node->state(), SarStatus::STATE_WRITE_FAILED);  // (same unwritable root again)
    EXPECT_TRUE(node->handle_discard().accepted);
}

TEST_F(NodeTest, ExistingFolderIsRetriedOnceAtSuffixedPath) {
    const std::string base = fresh_dir("ex");
    auto node = make_fake_node(base, base + "/runs");
    const auto r = node->handle_start(false, "", 0);
    ASSERT_TRUE(r.accepted) << r.message;
    fs::create_directories(r.run_dir);  // someone creates it while the run is in flight
    node->join_worker();
    ASSERT_EQ(node->state(), SarStatus::STATE_DONE) << node->make_status().fatal_error;
    const auto st = node->make_status();
    EXPECT_EQ(st.run_dir, r.run_dir + "_2");
    EXPECT_TRUE(fs::exists(st.run_dir + "/iq.sc16"));
    EXPECT_TRUE(st.fatal_error.empty());
}

TEST(SpscRing, DropsWhenFullNeverBlocksAndKeepsOrder) {
    sar_b210_ros::SpscRing<int> ring(4, [] { return 0; });
    for (int i = 0; i < 4; ++i) {
        int* s = ring.try_acquire();
        ASSERT_NE(s, nullptr);
        *s = i;
        ring.commit();
    }
    EXPECT_EQ(ring.try_acquire(), nullptr);
    EXPECT_EQ(ring.fill(), 4u);
    EXPECT_EQ(ring.max_fill(), 4u);
    for (int i = 0; i < 4; ++i) {
        int* s = ring.peek();
        ASSERT_NE(s, nullptr);
        EXPECT_EQ(*s, i);
        ring.pop();
    }
    EXPECT_EQ(ring.peek(), nullptr);
    EXPECT_NE(ring.try_acquire(), nullptr);
}

TEST(SpscRing, ConcurrentProducerConsumer) {
    sar_b210_ros::SpscRing<uint64_t> ring(8, [] { return uint64_t{0}; });
    constexpr uint64_t kN = 200000;
    std::thread consumer([&] {
        uint64_t expect = 0;
        while (expect < kN) {
            if (uint64_t* s = ring.peek()) {
                ASSERT_EQ(*s, expect);
                ++expect;
                ring.pop();
            }
        }
    });
    for (uint64_t i = 0; i < kN;) {
        if (uint64_t* s = ring.try_acquire()) {
            *s = i++;
            ring.commit();
        }
    }
    consumer.join();
}
