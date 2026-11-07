# 4 - Troubleshooting

## Setup and files (checked)

| Symptom | Cause and fix |
|---|---|
| Doctor prints `WARN governor` | Advice only. `sudo cpupower frequency-set -g performance` if you want it; captures run without. |
| Doctor `--check` exits 1, `missing: ...` | Run `python3 tools/setup/host_setup.py --dry-run`, then `sudo python3 tools/setup/host_setup.py --apply`. |
| `ModuleNotFoundError: b210_sar` | Use `uv run` from the repo root, not bare `python3` (`uv sync --extra gui` once). |
| GUI: "path is outside the data roots" (403) | Add the directory with `--data-root DIR`. |
| GUI: "not found" (404) | A relative path is resolved against each data root, not your shell's directory; use an absolute path. |
| File view shows horizontal stripes, no targets | Leakage dominates: set "leakage path (m)" and tick "remove static" ([tutorial 2](02_gui_on_files.md)). |
| `--check` says `INVALID` | Read the `error:` lines; errors name the key and the limit. Warnings do not block saving. |
| Port in use | Choose another with `--port`, or set `CONSOLE_B210_GUI_PORT`. |

## Pending hardware runs (b210-15/16/18)

Entries from real captures and live runs (USB, timing, overruns, ROS 2) are added after those runs. None are written from guesswork.
