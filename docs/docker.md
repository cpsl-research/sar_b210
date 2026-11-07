# Docker: capture, ROS 2 node and GUI in one image

One image, `sar_b210:dev`, holds the C++ capture (`sar_capture`, Release), the `sar_b210` ROS 2 node (jazzy), the GUI and the shipped configs, so a machine with only Docker and a B210 can run all three with no console, compiler, ROS or `uv` on the host. Files: `docker/app/{Dockerfile,compose.yaml,entrypoint.sh,setup_env.sh,.env.example}`. Base: `ubuntu:24.04` plus `ros-jazzy-ros-base` and apt UHD.

Nothing here transmits. `capture` is RX-only only when you pass `--rx-only`; the compose `capture` default is `--check` (no radio opened), and the node transmits only if a Start request says `transmit: true` and `confirm: "TRANSMIT"`.

## Prerequisites

Linux with Docker Engine and the compose plugin (your user in the `docker` group). Clone the repo; nothing else is needed.

## Build

```bash
docker compose -f docker/app/compose.yaml build demo
```

All services share the image and each sits in a compose profile, so name one. The build compiles the capture and the colcon packages, runs `ctest` and `colcon test` inside the image (a failing test fails the build), and downloads the B2xx FPGA/firmware images into `/usr/share/uhd/images`. It needs network; running the image does not. Your host `build/`, `configs/user/` and `runs/` never enter it.

## Demo, no radio

```bash
docker compose -f docker/app/compose.yaml --profile demo up demo
curl http://127.0.0.1:8010/api/health     # {"ok":true}
docker compose -f docker/app/compose.yaml --profile demo down
```

The GUI (`--source none`: Configs tab and file view) on loopback only, no device, no ROS traffic. `GUI_PORT=18010 ...` moves it.

## Hardware (Linux host)

```bash
docker/app/setup_env.sh      # writes docker/app/.env: uid/gid, USB group, USB mapping, run folder, GUI host/port
docker compose -f docker/app/compose.yaml --profile hw run --rm capture --check /app/configs/default.yaml
docker compose -f docker/app/compose.yaml --profile hw run --rm capture \
    /app/configs/default.yaml /runs/NAME --rx-only --duration 10      # RX-only; RUN_DIR must not exist
docker compose -f docker/app/compose.yaml --profile hw up node        # ROS 2 node, topics b210/...
docker compose -f docker/app/compose.yaml --profile hw up gui         # GUI, http://127.0.0.1:8010/
```

Run folders appear in `RUN_ROOT` on the host (`/runs` in the container). The image's commands are `capture`, `node` and `gui` (`docker/app/entrypoint.sh`); anything else is exec'd, so `--entrypoint bash` gives a shell. The GUI has no authentication: keep `GUI_HOST` on loopback.

**USB passthrough.** The B210 re-enumerates (new device number) after the FPGA image loads, so mapping one `/dev/bus/usb/BBB/DDD` node goes stale mid-run. The radio services bind-mount the whole `/dev/bus/usb` tree and allow USB character devices with `device_cgroup_rules: ["c 189:* rwm"]`; the container user also gets `USB_GID`, the group that owns the node (`setup_env.sh` reads it; plugdev, 46, on Ubuntu, though the host's `60-uhd-host.rules` normally makes the node world-accessible). The services are not `privileged`. `USB_DEV` in `.env` is the host side of the mount.

**Realtime and locked memory.** The radio services set `ulimits` `memlock: -1`, `rtprio: 99` and `cap_add: [SYS_NICE]`. These cannot exceed the host's own limits, so a host without the `limits.d` entry still runs out of lockable RAM: the doctor's current-session check reports it.

**Network.** The hardware services use host networking, so ROS 2 DDS discovery works with nodes on the host or other machines; `demo` publishes only `127.0.0.1:${GUI_PORT}`.

## What stays on the host

A container shares the host kernel, so these are set on the host, not in Docker: udev (`61-b210-power.rules`, the UHD rules), `limits.d` (memlock, rtprio), USB autosuspend. Run the doctor and apply them there:

```bash
python3 tools/setup/host_setup.py               # report
sudo python3 tools/setup/host_setup.py --apply --scope host
```

(`--scope deps`, the apt packages and FPGA images, is what the image already contains.) After a USB re-plug no recreate is needed because the whole tree is mounted.

## Troubleshooting

- `No UHD Devices Found` / permission denied: the host udev rules are missing (`host_setup.py` reports it), or the container user lacks the node's group (`USB_GID` in `.env`; re-run `setup_env.sh` with the radio plugged in).
- Overflows, `mlock` or priority warnings: the host limits (above); check `ulimit -l -r` inside with `docker compose ... run --rm --entrypoint bash capture -c 'ulimit -l -r'`.
- `up` says no such service: the services sit in profiles; add `--profile demo` or `--profile hw`.
- `runs/` or `configs/user/` root-owned: Docker created it before `setup_env.sh` did; `sudo chown` it once.
- The image is large (ROS base, UHD, its images); `docker image ls sar_b210` shows the size.

## Not covered

Registry publishing, arm64, macOS/Windows (USB passthrough and host networking do not work through Docker Desktop), TX enablement. The `hw` profile was config-checked, then an RX-only container capture ran on hardware on 2026-11-07 (10 s, 651 bursts, 0 invalid, transmit false; the node started idle in the container). See `docs/runbook.md` for field use.
