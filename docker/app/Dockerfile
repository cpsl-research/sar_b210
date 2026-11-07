# syntax=docker/dockerfile:1
# sar_b210: C++ capture, the sar_b210 ROS 2 node (jazzy) and the web GUI in one image. Build from the repo root:
#   docker compose -f docker/app/compose.yaml build demo   (all services share the image; each is profiled, so name one)
# Needs network at build time (apt, ROS apt repo, uhd_images_downloader, PyPI); none at run time.
# Nothing in the image transmits: the capture command is RX-only unless you drop --rx-only yourself (docs/docker.md).

ARG UV_VERSION=0.12.23

# ---- ros: ubuntu 24.04 + ROS 2 jazzy base (shared by build and runtime) ----
FROM ubuntu:24.04 AS ros
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu noble main" \
      > /etc/apt/sources.list.d/ros2.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends ros-jazzy-ros-base \
 && rm -rf /var/lib/apt/lists/*

# ---- build: capture (Release) + colcon ros2 packages; the build trees stay in this stage ----
FROM ros AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake ninja-build libuhd-dev libyaml-cpp-dev \
      libboost-system-dev python3-colcon-common-extensions ros-jazzy-ament-cmake-gtest \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /ws/src/sar_b210
COPY capture capture
COPY ros2 ros2
COPY configs configs
COPY tests/fixtures/config_parity.yaml tests/fixtures/config_parity.yaml
RUN cmake -S capture -B /build/capture -G Ninja -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /build/capture
RUN . /opt/ros/jazzy/setup.sh \
 && cd /ws && colcon build --packages-up-to sar_b210_ros --build-base /build/colcon \
      --install-base /opt/sar_b210_ws --cmake-args -DCMAKE_BUILD_TYPE=Release

# ---- test: the capture tests and the ROS node tests (no radio; also forced by runtime, so a default build runs them) ----
FROM build AS test
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN { ctest --test-dir /build/capture --output-on-failure \
 && . /opt/sar_b210_ws/setup.sh && cd /ws \
 && colcon test --packages-select sar_b210_ros --build-base /build/colcon --install-base /opt/sar_b210_ws \
      --event-handlers console_direct+ \
 && colcon test-result --test-result-base /build/colcon --verbose; } 2>&1 | tee /test.log

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---- runtime ----
FROM ros AS runtime
# uhd-host: libuhd, the udev rule file and uhd_images_downloader. The B2xx FPGA/firmware images go into
# /usr/share/uhd/images now, so the container needs no network at run time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3.12 uhd-host libyaml-cpp0.8 libboost-system1.83.0 \
 && uhd_images_downloader -t b2xx -i /usr/share/uhd/images \
 && rm -rf /var/lib/apt/lists/* /tmp/* \
 && userdel -r ubuntu 2>/dev/null; useradd -m -u 1000 -s /bin/bash sar
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_PREFERENCE=only-system UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONPATH=/app/src HOME=/tmp
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gui --no-install-project
COPY src src
COPY configs configs
COPY tests/fixtures/config_parity.yaml tests/fixtures/config_parity.yaml
COPY --from=build /build/capture/sar_capture /build/capture/sar_bench_probe /usr/local/bin/
COPY --from=build /opt/sar_b210_ws /opt/sar_b210_ws
COPY docker/app/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# ties the test run to this stage: the runtime image cannot be built if the tests fail
COPY --from=test /test.log /usr/share/doc/sar-b210-tests.log
RUN mkdir -p /runs /app/configs/user && chmod 1777 /runs /app/configs/user
USER sar
EXPOSE 8010
ENTRYPOINT ["docker-entrypoint.sh"]
# 0.0.0.0 is the container side; compose publishes it on 127.0.0.1 only
CMD ["gui", "--host", "0.0.0.0", "--port", "8010"]
