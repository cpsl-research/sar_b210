# b210_sar

Proof-of-concept vehicle-mounted stepped-frequency SAR on a USRP B210.
The spec is [docs/design_outline.pdf](docs/design_outline.pdf). Read it before changing behavior.

## Rules from the design outline

- Values marked *start value* are tunable defaults.
- Values marked *measure* must be measured on the hardware before they are relied on.
- Fixed constraints (B210, 2 antennas, no RF hardware, stepped chirp, ground vehicle,
  laptop + USB storage) are owner decisions. Ask before changing them.
- Owner decisions in docs/design_notes.md override the outline, including the 2.4 GHz ISM band and
  positioning by the autonomy stack: no IMU, attitude or lever-arm code in this repo.
- Open questions in [docs/design_notes.md](docs/design_notes.md) need an owner answer before final configuration.
- Work in task order T1-T10 (see README), building T9 early. Do not advance a validation
  stage until the current one passes.

## Invariants

- Each sub-band burst is its own pulse at its own position. Never stitch a sweep into one
  profile at one position (no stop-and-go), except for parked tests.
- Geometry is bistatic: R = |p_tx - x| + |p_rx - x|.
- Burst timestamps, not host clock readings, are the ground truth for pulse timing.
- Bad bursts are marked invalid in metadata, never silently dropped or shifted.
- Device-specific values live in config only, so the same code can run on an X310.
- Never transmit outside `permitted_band` in the config or above its EIRP cap.

## Commands

    uv sync                                       # Python environment
    uv run pytest                                 # tests
    cmake -B build -G Ninja && cmake --build build  # capture programs

C++ capture uses system UHD 4.6. Python processing must not import `uhd`: the system
`python3-uhd` does not match the project interpreter, and processing has to run with no radio.
