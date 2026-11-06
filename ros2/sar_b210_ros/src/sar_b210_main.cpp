#include <memory>

#include <rclcpp/rclcpp.hpp>

#include "sar_b210_ros/sar_b210_node.hpp"

int main(int argc, char** argv) {
    rclcpp::init(argc, argv);
    auto node = std::make_shared<sar_b210_ros::SarB210Node>();
    rclcpp::spin(node);
    node.reset();
    rclcpp::shutdown();
    return 0;
}
