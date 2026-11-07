#!/usr/bin/env bash
# One image, three commands: capture | node | gui. Anything else is exec'd as is.
#   capture ARGS   sar_capture ARGS  (e.g. /app/configs/default.yaml /runs/NAME --rx-only --duration 10)
#   node ARGS      ros2 launch sar_b210_ros sar_b210_launch.py ARGS  (run_root defaults to /runs)
#   gui ARGS       python -m b210_sar.gui ARGS  (data root defaults to /runs)
set -e
set +u
. /opt/ros/jazzy/setup.bash
. /opt/sar_b210_ws/setup.bash
cmd=${1:-gui}; [ $# -gt 0 ] && shift
case "$cmd" in
  capture) exec sar_capture "$@" ;;
  node)    exec ros2 launch sar_b210_ros sar_b210_launch.py run_root:=/runs "$@" ;;
  gui)     exec python -m b210_sar.gui --data-root /runs "$@" ;;
  *)       exec "$cmd" "$@" ;;
esac
