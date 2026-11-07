# 01 - The B210 GUI (stub)

A short start guide for the browser GUI. The full tutorials come later; this
page covers only what the GUI does today: browse, check and save configs.
The GUI does not open the radio and cannot transmit.

## Start it

From the repo root:

    uv sync --extra gui
    env -u PYTHONPATH uv run --extra gui python -m b210_sar.gui --port 8010

Open `http://localhost:8010/`. Options: `--host` (default `0.0.0.0`, no
authentication), `--port` (default 8010, or `CONSOLE_B210_GUI_PORT`),
`--source none` (the only source so far), `--config-root` (use another
`configs/` directory). Check it is up:

    curl http://localhost:8010/api/health      # {"ok":true}

## Configs tab

The list shows the shipped configs and your own, each with a badge for
valid / warnings / errors, and the derived numbers (planned bandwidth, etc.).

1. **Shipped configs are read-only.** Select `default` to view its YAML and
   derived numbers. It is never written; saving under a shipped name is
   refused (409).
2. **Copy to new.** Pick a config and press Copy to new. A form offers the
   stepped-waveform fields: `num_steps`, `step_spacing_hz`,
   `chirp_bandwidth_hz`, `chirp_length_s`, `dwell_s`, `taper_fraction`,
   `sample_rate_hz`, `rx_gain_db`, `tx_gain_db`. Comments in the YAML are kept.
3. **Check.** Validates the edited YAML and shows the issues and derived
   numbers. Nothing is written.
4. **Save.** Writes a new file in `configs/user/` (refused if the name
   exists or the config has errors).
5. **Delete.** Removes a user config after a confirmation prompt. Shipped
   configs cannot be deleted.

## Hardware limits: errors vs warnings

Checks use `configs/hw_limits.yaml`.

- **Error** - a chip or UHD limit (for example `tx_gain_db` above the cap).
  The config cannot be saved.
- **Warning** - a USB or library-level limit that may be exceeded in practice
  (for example a sample rate the link may not sustain). It saves, with the
  warning shown.

## Where files live

User configs are in `configs/user/` (gitignored, except its `README.md`).
Shipped configs are the other files in `configs/`.
