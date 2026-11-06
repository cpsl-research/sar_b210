// Radio-free tests: nothing here opens a radio. The radio factory is a counter that throws.
#include <gtest/gtest.h>

#include <atomic>
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
