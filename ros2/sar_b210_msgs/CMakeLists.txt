cmake_minimum_required(VERSION 3.8)
project(sar_b210_msgs)

find_package(ament_cmake REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/SarBurst.msg"
  "msg/SarClockSync.msg"
  "msg/SarStatus.msg"
  "srv/Start.srv"
  DEPENDENCIES std_msgs
)

ament_package()
