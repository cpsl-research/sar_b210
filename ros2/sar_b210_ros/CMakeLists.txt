cmake_minimum_required(VERSION 3.23)
project(sar_b210_ros)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(std_srvs REQUIRED)
find_package(sar_b210_msgs REQUIRED)

# The capture library lives at ../../capture (COLCON_IGNORE there keeps colcon from treating it as
# a package). Build it here as a subproject: just the library, no apps or tests.
set(SAR_BUILD_APPS OFF CACHE BOOL "" FORCE)
set(SAR_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../capture ${CMAKE_CURRENT_BINARY_DIR}/capture)

add_library(sar_b210_node_lib STATIC src/sar_b210_node.cpp)
target_include_directories(sar_b210_node_lib PUBLIC include)
target_link_libraries(sar_b210_node_lib PUBLIC sar::capture)
ament_target_dependencies(sar_b210_node_lib PUBLIC rclcpp std_msgs std_srvs sar_b210_msgs)

add_executable(sar_b210_node src/sar_b210_main.cpp)
target_link_libraries(sar_b210_node PRIVATE sar_b210_node_lib)
install(TARGETS sar_b210_node DESTINATION lib/${PROJECT_NAME})

install(DIRECTORY launch DESTINATION share/${PROJECT_NAME}/)
install(DIRECTORY ../../configs/ DESTINATION share/${PROJECT_NAME}/config FILES_MATCHING PATTERN "*.yaml")

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(sar_b210_ros_tests test/test_node.cpp)
  target_link_libraries(sar_b210_ros_tests sar_b210_node_lib)
  target_compile_definitions(sar_b210_ros_tests PRIVATE SAR_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../..")
endif()

ament_package()
