"""Reader for console HDF5 recordings carrying a SarBurst stream (Sensors hdf5_format.md, B210 streams).

Needs h5py (the `gui` optional extra). SarBurst groups are found by their stored `msg_type` attr, not
by name, so device-prefixed groups (`<name>_bursts`) work; `stream=` picks one when there are several.
When the stream has no `iq` dataset (node ran with publish_iq:=false), IQ is read from the run folder
named by the `b210_run_dir` attr, by `sample_offset`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.typing as npt
import yaml

from b210_sar.config import ConfigError, parse_config
from b210_sar.dataset import (
    BURST_COLUMNS,
    BURST_DTYPE,
    CLOCK_SYNC_COLUMNS,
    CLOCK_SYNC_DTYPE,
    IQ_FILE,
    RunFolderSource,
    SweepSource,
)

try:
    import h5py
except ImportError as e:  # pragma: no cover
    raise ImportError("reading HDF5 recordings needs h5py: install the extra (`uv sync --extra gui`)") from e

BURST_TYPE = "sar_b210_msgs/msg/SarBurst"
CLOCK_TYPE = "sar_b210_msgs/msg/SarClockSync"


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def find_streams(f: "h5py.File", msg_type: str = BURST_TYPE) -> list[str]:
    """Names of root groups whose `msg_type` attr equals `msg_type`."""
    return [k for k, g in f.items() if isinstance(g, h5py.Group) and _s(g.attrs.get("msg_type", "")) == msg_type]


class H5Source(SweepSource):
    def __init__(self, path: str | Path, stream: str | None = None):
        self.path = Path(path)
        self._f = h5py.File(self.path, "r")
        try:
            self._open(stream)
        except Exception:
            self._f.close()
            raise

    def _open(self, stream: str | None) -> None:
        f = self._f
        names = find_streams(f)
        if not names and "b210_bursts" in f:  # tolerate a group missing its attrs
            names = ["b210_bursts"]
        if stream is not None:
            if stream not in f:
                raise KeyError(f"{self.path}: no group {stream!r}; SarBurst streams: {names}")
            names = [stream]
        if not names:
            raise ValueError(f"{self.path}: no SarBurst ({BURST_TYPE}) stream")
        if len(names) > 1:
            raise ValueError(f"{self.path}: several SarBurst streams {names}; pass stream=")
        self.stream = names[0]
        g = f[self.stream]
        self._g = g
        n = len(g["index"])
        b = np.zeros(n, BURST_DTYPE)
        for c in BURST_COLUMNS:
            b[c] = g[c][:]
        self.bursts = b
        self._iq = g["iq"] if "iq" in g else None

        text = _s(g.attrs.get("b210_config_text", ""))
        self.config_text = text
        self.config = None
        notes: list[str] = []
        if text.strip():
            cfg, issues = parse_config(yaml.safe_load(text))
            if cfg is None:
                raise ConfigError(issues)
            self.config = cfg
        else:
            notes.append(f"b210_config_status={_s(g.attrs.get('b210_config_status', '?'))}: no config text")

        self.clock_sync = None
        cname = self._clock_group()
        if cname is not None:
            d = f[cname]["data"][:]
            cs = np.zeros(len(d), CLOCK_SYNC_DTYPE)
            for k, c in enumerate(CLOCK_SYNC_COLUMNS):
                cs[c] = d[:, k]
            self.clock_sync = cs
        self.replica = None

        self._fallback: RunFolderSource | None = None
        self._fb_rows: dict[int, int] = {}
        run_dir = _s(g.attrs.get("b210_run_dir", ""))
        self.run_dir = run_dir
        if self._iq is not None:
            self.iq_source = "hdf5"
        else:
            self.iq_source = "none"
            if run_dir and (Path(run_dir) / IQ_FILE).is_file():
                self._fallback = RunFolderSource(run_dir)
                self.iq_source = "run_dir"
                notes.append(f"iq absent; read from run folder {run_dir}")
                offs = {int(o): r for r, o in enumerate(self._fallback.bursts["sample_offset"])}
                for r in range(len(b)):
                    if int(b["sample_offset"][r]) in offs:
                        self._fb_rows[r] = offs[int(b["sample_offset"][r])]
                if self.config is None:
                    self.config = self._fallback.config
            else:
                notes.append(f"iq absent and run folder {run_dir!r} not usable: metadata only")
        self._finish_init(notes=notes)

    def _clock_group(self) -> str | None:
        names = find_streams(self._f, CLOCK_TYPE)
        if not names and "b210_clock_sync" in self._f:
            names = ["b210_clock_sync"]
        if len(names) > 1:  # prefer the one sharing the burst group's device prefix
            prefix = self.stream[: -len("bursts")] if self.stream.endswith("bursts") else None
            names = [n for n in names if prefix is not None and n.startswith(prefix)] or names
        return names[0] if names and "data" in self._f[names[0]] else None

    def _has_iq(self, row: int) -> bool:
        n = int(self.bursts["num_samples"][row])
        if self._iq is not None:
            return self._iq.shape[1] >= 2 * n
        fr = self._fb_rows.get(row)
        return fr is not None and self._fallback._has_iq(fr) and int(self._fallback.bursts["num_samples"][fr]) == n

    def _raw(self, row: int) -> npt.NDArray[np.int16]:
        if self._iq is not None:
            return self._iq[row, : 2 * int(self.bursts["num_samples"][row])]
        return self._fallback._raw(self._fb_rows[row])

    def close(self) -> None:
        if self._fallback is not None:
            self._fallback.close()
        try:
            self._f.close()
        except Exception:
            pass
