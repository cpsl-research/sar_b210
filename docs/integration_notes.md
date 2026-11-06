# Autonomy stack integration notes

What to know when embedding the radar capture in the autonomy stack. Measurements behind these
notes are in [design_notes.md](design_notes.md#b210-measurements). `README.md` has a code example.

## Status

| Item | State |
|---|---|
| Capture on the B210, cabled loopback | Working; validation stage 1 passed (300 s, 16404 bursts, none invalid) |
| `host_clock` sync | Tested |
| `pps` sync | Written but never run; nothing is wired to the PPS input |
| Over-the-air operation | Not tested |
| Python processing and simulator | Stubs |

## Do before going over the air

1. Measure conducted power and harmonics with a spectrum analyzer. The cabled loopback checks neither,
   and UHD gain settings are not calibrated in dBm.
2. Stay within the FCC Part 15 limit assumed in the config: -9.25 dBm average conducted with 8 dBi
   antennas, or about 3.3 dBm during a burst if duty-cycle averaging applies. Raise `max_tx_gain_db`
   only after measuring. It is 40 dB until then.
3. Repeat validation stage 2 with the horns attached. Real leakage will be weaker and behave differently
   from the cable, and `settle_margin_s` still has to be set from its phase stability. If it is too weak
   or unstable, the outline's fallback is a cabled reference on channel B.

Never connect TX directly to RX2. With the 10 dB attenuator in line, keep TX gain at or below the cap:
near full gain the RX port's limit of about -15 dBm could be exceeded. The cap only applies to
`sar_capture` and `CaptureSession`, not to other tools such as `uhd_siggen`.

## Host setup

`scripts/check_host.sh` reports most of these without changing anything.

- **`UHD_IMAGES_DIR`:** not needed with apt UHD, which finds its images in `/usr/share/uhd/images`. Set it
  only for a non-default images directory (for example one filled by `uhd_images_downloader`); without
  the images the B210 will not load after a power cycle.
- **Thread priority:** the `rtprio` limit is 0, so streaming threads cannot raise their priority. There
  were no overflows without it on an idle host, but the stack adds CPU load. Retest under real load.
- **Power and USB:** use the performance CPU governor, disable USB autosuspend, and run on AC power.
  Autosuspend can be off globally (`/sys/module/usbcore/parameters/autosuspend` = -1) or per device
  (`power/control` = `on` on the Ettus device, 2500:002x, for example by a udev rule);
  `scripts/check_host.sh` accepts either. The original bench host had none of these set.
- **USB:** keep the B210 on its own USB 3 controller with no hub. It enumerated at 5 Gb/s on bus 4 here.
- **Storage:** write datasets to the USB SSD. `/home` has about 2.5 GB free. A 60 s run is about 0.74 GB,
  and `write_dataset` refuses to start if space is short.
- **Memory lock:** the RAM buffer is page-locked when the `memlock` limit allows, and logs a warning if
  not. The default config needs 0.92 GB.

## Linking

- Use `add_subdirectory()` on this repo's `capture/` directory and link `sar::capture`. Apps and tests
  stay off by default when it is not the top-level project. `capture/` holds the only `CMakeLists.txt`
  and a `COLCON_IGNORE`, so colcon can crawl the repo to the ROS packages without treating the
  library as a package of its own.
- It needs C++17, system UHD 4.6, Boost and yaml-cpp. It is a static library.
- CMake stops if a dependency resolves inside conda, because system UHD is built against system Boost.
  Check that the stack's environment does not pull in a different Boost.
- Python processing does not import `uhd`, and the system `python3-uhd` does not match the project's
  interpreter. All radio access goes through C++.

## Using `CaptureSession`

- **One run per session.** Create a new session and a new radio for each run. Only one process can hold
  the B210.
- **Blocking calls:** the constructor allocates and touches the whole RAM buffer. `make_uhd_radio` can
  take seconds if it has to load the FPGA. `write_dataset` syncs to disk and hashes every file. Keep all
  of these off any time-critical stack thread.
- **Thread safety:** `status()` and `request_stop()` are safe from any thread. Read `bursts()`,
  `clock_sync()` and `fatal_error()`, and call `write_dataset()`, only after `wait()`. `status()` also
  carries live sweep period, burst gap, RAM use and callback timing.
- **Live data:** `CaptureOptions::on_burst` and `on_clock_sync` deliver each burst (record plus its IQ)
  and each clock-sync row during the run, on the timing thread. The IQ pointer is valid only during the
  call. Copy and return: no blocking, contended locks or heavy allocation. The full contract is in
  `capture_session.hpp`.
- **Signals:** the library installs no signal handlers. The stack decides how a stop is requested.
- **Ways a run ends:** `request_stop()`, `timing.max_run_s`, a full RAM buffer, or a radio failure. After
  a failure, `fatal_error()` is set and the bursts captured so far can still be written.
- **Failed writes:** `write_dataset` never overwrites. If it throws, the data stays in RAM, so call it
  again with another path.
- **Edited configs:** `config.yaml` in a dataset is `RunConfig::source_text`, the file as loaded. If the
  stack changes fields in code, as `--duration` does with `max_run_s`, the dataset's copy will not show
  it. Log the change with `RunLog::add`, or update `source_text` too.
- **UHD logging:** `make_uhd_radio` registers a global UHD logger under the key `b210_sar`. If the stack
  also adds UHD loggers, use a different key.
- **Transmit:** pass the same value to `make_uhd_radio` and `CaptureOptions`. With `false` the radio has
  no TX streamer and cannot emit. This is the safe way to run the stack before the RF checks are done.
- **Preflight:** `start()` tunes every step, checks the band and LO lock, and throws before transmitting
  if a check fails. Catch this at startup.

## Matching positions to bursts

Positions are the stack's job. This repo has no IMU, attitude or lever-arm code.

- **Burst time:** `usrp_ticks / master_clock_rate_hz` is the first sample. The center, which is the time
  to evaluate position at, is that plus `num_samples / (2 * sample_rate_hz)`, or 0.5 ms.
- **Timestamps:** `usrp_ticks` is the time reported with the first sample. Use it, not host clock
  readings taken around a burst.
- **Clock conversion:** interpolate through all `clock_sync.csv` rows, not one offset. The USRP and host
  monotonic clocks drifted 5.1 ppm over 300 s. Rows are logged about once a second.
- **Clock error:** host times are the midpoint of the USRP time query. `round_trip_ns` bounds the error,
  with a median of 0.22 ms and a worst case of 0.51 ms. That is about 0.25 mm at 0.5 m/s.
- **Epoch:** in `host_clock` mode USRP time is reset to 0 when the radio opens, so ticks are only
  comparable within one session. Use `clock_sync.csv` to relate runs.
- **Invalid bursts:** skip rows with `valid` = 0. They keep their place in the file, and missing samples
  are zero. A burst with `lo_not_locked` was never transmitted or received, and its `usrp_ticks` is the
  time of the attempt.
- **Antenna positions:** give each antenna's phase center separately, because processing treats the
  geometry as bistatic: R = |p_tx - x| + |p_rx - x|. Each burst needs its own position; do not treat a
  sweep as one position.
- **Accuracy:** about 15 mm relative over the run, before autofocus (lambda/8). Relative smoothness
  matters more than absolute accuracy.

### Still to decide

The stack's output format and time base are not settled. `src/b210_sar/positions.py` assumes a file
`antenna_positions.csv` in the run folder, with one row per burst:

    index,tx_x,tx_y,tx_z,rx_x,rx_y,rx_z

Change that file and `POSITION_COLUMNS` if the stack delivers something else, such as a trajectory to
interpolate.

## Operating limits

| Limit | Value |
|---|---|
| Max speed | 1.12 m/s at lambda/3, for the budgeted 36.4 ms sweep; planned speed is 0.5 m/s |
| Sweep time | About 37 ms measured, with bursts up to 25.5 ms apart (normally 18.3 ms) |
| Data rate | 12.3 MB/s while running |
| Range resolution | 2 m |
| Azimuth resolution | About 12 cm at 20 m with a 10 m aperture |

The budget is recomputed from the config, so changing steps, dwell or settle changes these. Run
`sar_capture --check` to see the new values. CPU load that slows retuning stretches the sweep without
marking any burst invalid, so compare burst spacing in `bursts.csv` against the speed limit.

## Interference and the vehicle

- Wi-Fi and Bluetooth share 2400-2483.5 MHz, and mean subtraction will not remove them. Turn off these
  radios on the vehicle where possible, including any the stack uses, or check their effect on the noise floor.
- Mount the antennas rigidly, because the TX-RX geometry must not flex during a run.

## Values to recheck

These were measured on B210 serial 3271117 at 56 MS/s. Remeasure if the radio, UHD version or sample rate
changes.

| Config key | Value | How to recheck |
|---|---|---|
| `device.rx_timestamp_offset_ticks` | 7 | Every burst flags `rx_time_mismatch` if wrong; not yet checked across a power cycle |
| `timing.retune_s` | 0.0092 s | Burst spacing in `bursts.csv`, plus `sar_bench_probe` |
| `timing.command_lead_s` | 0.002 s | `rx_late` or `tx_late` flags mean it is too short |
| `timing.settle_margin_s` | 0.008 s | Not measured yet; set at stage 2 |
