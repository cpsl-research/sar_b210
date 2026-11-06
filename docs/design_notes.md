# Design notes and open questions

Companion to [design_outline.pdf](design_outline.pdf). Record answers here, then update
`configs/default.yaml`.

## Owner decisions that override the outline

- **2.4 GHz ISM band, FCC Part 15 (US), 2400-2483.5 MHz.** The waveform is 2 steps of 45 MHz chirps
  spaced 30 MHz, centered at 2441.75 MHz, occupying 2404.25-2479.25 MHz with a 4.25 MHz guard each side.
  With the measured retune time and an 8 ms settle: about 36 ms sweep, 2.0 m range resolution, about
  1.1 m/s limit at lambda/3, and about 12 cm azimuth resolution at 20 m with a 10 m aperture. This replaces the outline's 0.5 m range
  target, including the stage 3 mainlobe criterion.
- **Antennas: UWB PCB horns.** About 8 dBi and 60 degree beamwidth at 2.4 GHz.
- **2 m range resolution is acceptable** for this proof of concept.
- **Positioning comes from the autonomy stack.** IMU logging, attitude, lever arms and trajectory
  interpolation are out of scope here. The capture program has three threads, not four.
  It writes burst times and `clock_sync.csv`; the stack adds per-burst TX and RX positions.

Consequences to check:

- Position requirement is lambda/8, about 15 mm relative over the run, before autofocus.
- The 15.249 fundamental limit is 50 mV/m at 3 m, about -1.25 dBm average EIRP, and harmonics are
  limited to 500 uV/m. With 8 dBi horns, that is about -9.25 dBm average conducted power, so the outline's
  10 dBm is too high. Timed bursts at about 5.6% duty allow about 11.3 dBm EIRP, or 3.3 dBm conducted,
  during the burst, if averaging under 15.35 applies. UHD gain settings are not calibrated in dBm,
  so measure conducted power before raising `tx_gain_db`.
- With no filter, harmonics and out-of-band emissions may not meet the limits, including in the
  restricted band just above 2483.5 MHz. Measure radiated emissions with a spectrum analyzer before
  transmitting at anything above minimum gain.
- Wi-Fi and Bluetooth share this band and are not static, so mean subtraction will not remove them.
  Turn off radios on the vehicle where possible, and check the noise floor at stage 1.
- At 2 m resolution, leakage sidelobes can mask close targets, so keep targets well away from zero range.
- Directional horns side by side may leave little leakage, which is the phase reference. Check its level and stability at stage 2. The
  outline's fallback is a cabled reference on channel B.
- UWB antenna phase centers can shift with frequency, which leakage calibration may not fully
  remove. Check sub-band alignment on the corner reflector at stage 3.

## Open questions for the owner

- [ ] Autonomy stack interface: time base, output format, and whether positions are per burst.
      `POSITION_COLUMNS` in `positions.py` is provisional.
- [ ] Expected target ranges and scene type.

## Issues found while laying out the code

### LO offset cannot clear DC from a 45 MHz chirp at 56 MS/s

The outline tunes with an LO offset so the chirp band avoids DC. The chirp spans +/-22.5 MHz in
a +/-28 MHz digital band, so the shift can be at most about 5.5 MHz, and clearing DC needs more
than 22.5 MHz. Every sub-band will contain DC with these values.

Proposed: keep `lo_offset_hz: 0`. The matched filter spreads the DC spur across range, and it is
static, so per-step mean subtraction should remove most of it. Check this at validation stages 2-3.

### RX bursts must start on a chirp-period boundary

The leakage peak only stays in a fixed range window if RX is aligned to the TX chirps. The outline's
fixed window depends on this but does not state it. So:

- Capture starts TX one chirp period (1120 ticks at 56 MHz) before RX and runs it one chirp longer, so
  pipeline latency never leaves part of an RX chirp window empty.
- It captures a whole number of chirps per burst, so range compression can be circular.
- A TX underflow or late burst marks that burst invalid.
- Continuous TX is not implemented: it would make the duty cycle 100% and exceed the EIRP budget.

### Spacing rule and beamwidth

The outline's formula, lambda / (4 sin(theta/2)), gives lambda/2 at 60 degrees and reaches lambda/3
only near 97 degrees, while the outline says lambda/3 assumes about 60 degrees or wider. Lambda/3
is conservative at 60 degrees. The budget tool should report both.

## B210 measurements

Serial 3271117, UHD 4.6.0, USB 3, cabled loopback TX/RX to RX2 through 10 dB, TX gain 20 dB, RX gain 30 dB.
Validation stage 1 passed: 300 s with TX and RX running, 16404 bursts, none invalid.

- Tune (TX and RX together): median 6.6 ms, p99 11.7 ms, worst 78 ms over 500. LO lock check: under 1 ms.
- Burst spacing: median 18.3 ms, p99 21.8 ms, worst 25.5 ms, so a sweep takes about 37 ms.
- First RX sample arrives 7 ticks after the commanded tick, on every burst of every run so far. Not yet
  checked across a power cycle; a change shows up as `rx_time_mismatch` on every burst.
- The cabled path's peak stays in one range bin (159 samples, 2.84 us latency) in every burst.
- LO phase is random after retune, as the outline predicted: consecutive-burst phase differences spread
  by about 100 deg.
- After normalizing at the peak, band-averaged phase repeats to 0.14 deg rms, worst burst 0.54 deg, and
  amplitude to 0.03 dB. This is a strong cabled signal; repeat with real antenna leakage at stage 2.
- The sample peak was 1.1% of full scale at these gains.
- USRP clock against host monotonic: 5.1 ppm drift over 300 s, which is why `clock_sync.csv` logs
  throughout a run. USRP time query round trip: median 0.22 ms, worst 0.51 ms.
- Thread priority could not be raised (rtprio limit is 0); no overflows without it.

## Working rules and invariants

Carried over from the removed `CLAUDE.md`.

- Values marked *start value* in the outline are tunable defaults. Values marked *measure* must be
  measured on the hardware before they are relied on.
- Fixed constraints (B210, 2 antennas, no RF hardware, stepped chirp, ground vehicle, laptop + USB
  storage) are owner decisions. Ask before changing them. Owner decisions in this file override the
  outline, including the 2.4 GHz ISM band and positioning by the autonomy stack (no IMU, attitude or
  lever-arm code in this repo).
- Work in task order T1-T10 (see README), building T9 early. Do not advance a validation stage until
  the current one passes.
- Each sub-band burst is its own pulse at its own position. Never stitch a sweep into one profile at one
  position (no stop-and-go), except for parked tests.
- Geometry is bistatic: R = |p_tx - x| + |p_rx - x|.
- Burst timestamps, not host clock readings, are the ground truth for pulse timing.
- Bad bursts are marked invalid in metadata, never silently dropped or shifted.
- Device-specific values live in config only, so the same code can run on an X310.
- Never transmit outside `permitted_band` in the config or above its EIRP cap.
