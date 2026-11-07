# 0 - Install and the host doctor

Get a machine ready to build the capture, process runs and use the GUI. Nothing here opens the radio. Ubuntu with `uv`, `git` and `python3` is assumed. Reference: the repo [README](../../README.md).

## Get the repo and the Python side

```bash
git clone https://github.com/cpsl-research/sar_b210.git && cd sar_b210
uv sync --extra gui          # Python tooling plus the GUI's dependencies (fastapi, h5py)
uv run pytest -q             # no hardware needed
```

Inside console the repo is already checked out at `src/sar_b210` (a submodule of `CPSL_ROS2_Sensors`); run everything below from there.

## The host doctor

`tools/setup/host_setup.py` checks, and with `--apply` fixes, what the B210 needs from the host:

| Check | What it wants |
|---|---|
| `apt` | `libuhd-dev uhd-host libyaml-cpp-dev libboost-system-dev cmake ninja-build` |
| `images` | `usrp_b210_fpga.bin`, `usrp_b200_fw.hex` in `/usr/share/uhd/images` |
| `udev-uhd`, `udev-power` | UHD's udev rule, and `61-b210-power.rules` (keeps USB power on) |
| `limits` | `memlock` unlimited and `rtprio` 99 for your user (log out and in after) |
| `autosuspend` | `power/control=on` for the attached B210 |
| `governor` | CPU governor `performance` (warn only) |
| `session`, `usb`, `ac` | Report only: limits active in this session, B210 on its own USB 3 link, laptop on AC |

Look first (changes nothing, never opens the radio):

```bash
python3 tools/setup/host_setup.py             # one line per check: OK / WARN / MISSING / N-A
python3 tools/setup/host_setup.py --check     # silent; exit 0 if ready, 1 and 'missing: ...' on stderr
scripts/check_host.sh                         # the same, plus the USB topology (want no hub)
```

A `WARN` is advice (the printout names the command to run by hand), not a failure. Fix what is missing:

```bash
python3 tools/setup/host_setup.py --dry-run                 # print every fix, run nothing
sudo python3 tools/setup/host_setup.py --apply              # asks once per check; --yes to skip asking
sudo python3 tools/setup/host_setup.py --scope deps --apply # apt and images only (also fine in a container)
python3 tools/setup/host_setup.py --only udev-power         # re-check one item
```

Log out and back in after the `limits` fix, then replug the B210 if the udev rules changed.

From console, the Sensors repo's shim `scripts/deps/b210.sh` wraps the same doctor: `bash b210.sh --check` (no root), `sudo bash b210.sh` for apt and images, `sudo bash b210.sh --host` for udev, limits and autosuspend. Its `--check` does not test that a B210 is attached, its USB speed or the image versions.

## Build the capture

```bash
cmake -S capture -B build -G Ninja && cmake --build build
./build/sar_capture_tests                         # fake radio, no hardware
./build/sar_capture --check configs/default.yaml  # validate and print the budget; no radio
```

If the FPGA images are not in `/usr/share/uhd/images`, `export UHD_IMAGES_DIR=/path/to/images` first. Running a capture (RX-only or TX) is a hardware procedure, not covered here: see the [README](../../README.md#capture) and `docs/runbook.md`.

Next: [1, the GUI and configs](01_gui.md).
