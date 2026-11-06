# ROS 2 node (sar_b210_ros, sar_b210_msgs)

`sar_b210` wraps `CaptureSession`: it streams every burst on `b210/bursts` while the run is live
and still writes the run folder at the end. RX-only unless a Start request carries
`transmit: true` and `confirm: "TRANSMIT"` (and the config passes the library preflight).

Build (the capture library is built in as a subproject; `capture/COLCON_IGNORE` keeps colcon off it):

    source /opt/ros/jazzy/setup.bash
    colcon build --packages-up-to sar_b210_ros     # from a workspace whose src/ holds this repo
    ros2 launch sar_b210_ros sar_b210_launch.py [config_file:=default.yaml run_root:=~/b210_runs publish_iq:=true]

| Topic / service | Type | Notes |
|---|---|---|
| `b210/bursts` | `SarBurst` | reliable; stamp = host realtime at burst centre; `iq` int16 I/Q interleaved (empty with `publish_iq:=false`) |
| `b210/status` | `SarStatus` | 2 Hz, also idle; state, rates, sweep period vs budget, queue fill/drops, `run_dir` |
| `b210/clock_sync` | `SarClockSync` | each clock-sync row |
| `b210/config` | `std_msgs/String` | latched; the run config text |
| `b210/start` | `Start` | `transmit`, `confirm`, `duration_s` (0 = config `max_run_s`) |
| `b210/stop` | `std_srvs/Trigger` | |

Params: `config_path`, `run_root`, `write_run_folder` (true), `publish_iq` (true),
`publish_queue_depth` (192 bursts, about 2.9 s at the measured 65 bursts/s of 224 KB, 43 MB).
The burst callback copies into a preallocated lock-free ring and never blocks; a publisher thread
drains it. A full queue drops the burst from the topic (never from the run folder) and counts it
in `SarStatus.publish_drops`.
