# b210_sar

Short-range, vehicle-mounted stepped-frequency SAR on a USRP B210: two antennas, no RF
front end, positions from an external autonomy stack, offline backprojection. Success is a recognizably focused image
of strong targets from a short, slow drive.

Spec: [docs/design_outline.pdf](docs/design_outline.pdf).
Open issues: [docs/design_notes.md](docs/design_notes.md).
Integrating with the autonomy stack: [docs/integration_notes.md](docs/integration_notes.md).

**Status:** the C++ capture works and has been tested on a B210 in cabled loopback, not yet over the
air. Python processing and the simulator are still stubs.

## Capture

    # Only if the FPGA images are not in apt UHD's default /usr/share/uhd/images:
    #   export UHD_IMAGES_DIR=/path/to/images   # e.g. where uhd_images_downloader put them
    cmake -S capture -B build -G Ninja && cmake --build build
    ./build/sar_capture_tests                             # no hardware needed

    ./build/sar_capture --check configs/default.yaml      # validate and print the budget; no radio
    ./build/sar_bench_probe configs/default.yaml          # retune and lock timing; never transmits
    ./build/sar_capture configs/default.yaml RUN_DIR --rx-only --duration 60   # nothing transmitted
    ./build/sar_capture configs/default.yaml RUN_DIR --duration 40             # TRANSMITS

`RUN_DIR` must not already exist. Capture refuses to run if the sweep leaves `permitted_band`, the
antenna gain is unset, or `tx_gain_db` exceeds `max_tx_gain_db`.

### Using it from another program

`add_subdirectory()` this repo and link `sar::capture`. Apps and tests are off by default when it is
not the top-level project. Everything is in `namespace sar`:

```cpp
#include "sar/capture_session.hpp"
#include "sar/uhd_radio.hpp"

sar::RunConfig config = sar::load_run_config("configs/default.yaml");
auto log = std::make_shared<sar::RunLog>();
sar::CaptureSession session(config, sar::make_uhd_radio(config, log, /*transmit=*/true), log, {true});
session.start();                  // throws before transmitting if preflight fails
// session.status() can be polled from any thread
session.request_stop();           // the run also ends at timing.max_run_s or on a radio failure
session.wait();
if (session.fatal_error()) { /* data captured so far is still writable */ }
session.write_dataset(run_dir);   // throws, keeping data in RAM, if the path exists or the disk is short
```

To place bursts on the stack's clock, use `session.bursts()` or `bursts.csv` for burst start ticks
(`usrp_ticks / master_clock_rate_hz`), and `session.clock_sync()` or `clock_sync.csv` for USRP time
against `CLOCK_MONOTONIC` and `CLOCK_REALTIME`. Each burst lasts `num_samples / sample_rate_hz`.

## Layout

    configs/default.yaml      Run config: the one source of parameters, copied into each dataset
    capture/                  C++ capture on UHD multi_usrp (runs on the vehicle)
      apps/                   sar_capture, sar_bench_probe
      include/sar/, src/      Library: config, budget, chirp, radio, session, dataset writer
      tests/                  Tests using a fake radio that injects faults
    src/b210_sar/             Python tooling and offline processing (no radio needed)
      config, chirp           Config schema, waveform for the simulator
      dataset, positions      Run folder I/O, per-burst antenna positions from the autonomy stack
      processing/             Pulse, calibration, backprojection, autofocus, pipeline
      sim                     Synthetic runs for testing processing without hardware
    tools/setup/host_setup.py Host doctor and installer: apt UHD, B2xx images, udev, limits, autosuspend
    scripts/check_host.sh     Read-only wrapper: the doctor plus the USB topology
    docs/                     Spec, design notes, field runbook
    tests/

## Task map

| Task | Deliverable | Where | Validation stage |
|---|---|---|---|
| T1 | Config schema and budget tool (done) | `capture/src/run_config.cpp`, `budget.cpp`, `configs/` | |
| T2 | Chirp generator (done) | `capture/src/chirp.cpp` | |
| T3 | Bench timing probe (done) | `capture/apps/sar_bench_probe.cpp` | 1 |
| T4 | Capture program (done) | `capture/` | |
| T5 | Clock log and position ingest (replaces IMU work) | `capture/`, `b210_sar/positions.py` | 4 |
| T6 | Range compression and calibration | `b210_sar/processing/pulse.py`, `calibration.py` | 2-3 |
| T7 | Backprojection imager | `b210_sar/processing/backprojection.py` | 5 |
| T8 | Autofocus | `b210_sar/processing/autofocus.py` | 5-6 |
| T9 | Simulator (build early) | `b210_sar/sim.py` | |
| T10 | Field runbook | `docs/runbook.md` | |

## GUI

A browser GUI for the config library (browse shipped and user configs, copy,
check against the B210 hardware limits, save to `configs/user/`). It never
opens the radio.

    uv sync --extra gui
    env -u PYTHONPATH uv run --extra gui python -m b210_sar.gui --port 8010

Then open `http://localhost:8010/`. Step-by-step guides: the tutorials below.
Screenshots of it can be taken with console's `tools/pagecap.py --url`; the
spec is `tools/gui_shots.json`.

## Tutorials

Short, copy-pasteable lessons in [`docs/tutorials/`](docs/tutorials/). Do 0 to 2 in order; none opens the radio.

0. [Install and the host doctor](docs/tutorials/00_install.md): clone, `uv sync`, `host_setup.py`, build the capture
1. [Configs and the GUI](docs/tutorials/01_gui.md): shipped vs user configs, hardware limits, the `b210_sar.configs` CLI, the Configs tab
2. [The GUI on recorded runs](docs/tutorials/02_gui_on_files.md): run folders, HDF5, the simulator
3. [The GUI live over ROS 2](docs/tutorials/03_gui_live_ros.md): pending hardware runs
4. [Troubleshooting](docs/tutorials/04_troubleshooting.md): setup and file errors; hardware entries pending

## Python

    uv sync && uv run pytest

C++ needs `libuhd-dev`, `libyaml-cpp-dev` and Boost.
