cmake_minimum_required(VERSION 3.23)
project(b210_sar_capture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Apps and tests build by default only when this is the top-level project, so another project
# can add_subdirectory() this one and link just the sar::capture library.
option(SAR_BUILD_APPS "Build sar_capture and sar_bench_probe" ${PROJECT_IS_TOP_LEVEL})
option(SAR_BUILD_TESTS "Build the capture tests" ${PROJECT_IS_TOP_LEVEL})

# System UHD is built against system Boost. An active conda env must not supply Boost or yaml-cpp.
if(DEFINED ENV{CONDA_PREFIX})
  list(APPEND CMAKE_IGNORE_PREFIX_PATH "$ENV{CONDA_PREFIX}")
endif()

find_package(UHD REQUIRED)
find_package(Boost REQUIRED COMPONENTS system)
find_package(yaml-cpp REQUIRED)
find_package(Threads REQUIRED)

foreach(dep_dir IN ITEMS "${UHD_DIR}" "${Boost_DIR}" "${yaml-cpp_DIR}")
  if(dep_dir MATCHES "conda")
    message(FATAL_ERROR "Dependency resolved inside conda: ${dep_dir}")
  endif()
endforeach()

add_library(sar_capture_core STATIC
  src/budget.cpp
  src/capture_session.cpp
  src/chirp.cpp
  src/dataset_writer.cpp
  src/ram_buffer.cpp
  src/run_config.cpp
  src/sha256.cpp
  src/uhd_radio.cpp
)
add_library(sar::capture ALIAS sar_capture_core)
target_include_directories(sar_capture_core PUBLIC include ${UHD_INCLUDE_DIRS})
target_link_libraries(sar_capture_core PUBLIC ${UHD_LIBRARIES} Boost::system yaml-cpp::yaml-cpp Threads::Threads)
target_compile_options(sar_capture_core PRIVATE -Wall -Wextra)
# Last-resort place to find hw_limits.yaml (see find_hw_limits_path): the repo's configs/ dir.
target_compile_definitions(sar_capture_core PRIVATE SAR_DEFAULT_CONFIG_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../configs")

if(SAR_BUILD_APPS)
  add_executable(sar_capture apps/sar_capture.cpp)
  target_link_libraries(sar_capture PRIVATE sar_capture_core)
  target_compile_options(sar_capture PRIVATE -Wall -Wextra)

  add_executable(sar_bench_probe apps/sar_bench_probe.cpp)
  target_link_libraries(sar_bench_probe PRIVATE sar_capture_core)
  target_compile_options(sar_bench_probe PRIVATE -Wall -Wextra)
endif()

if(SAR_BUILD_TESTS)
  enable_testing()
  add_executable(sar_capture_tests tests/capture_tests.cpp)
  target_link_libraries(sar_capture_tests PRIVATE sar_capture_core)
  target_compile_options(sar_capture_tests PRIVATE -Wall -Wextra)
  target_compile_definitions(sar_capture_tests PRIVATE SAR_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/..")
  add_test(NAME sar_capture_tests COMMAND sar_capture_tests)
endif()
