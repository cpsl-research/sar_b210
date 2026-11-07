"""Run folder format, shared with capture/src/dataset_writer.cpp."""

from __future__ import annotations

import csv
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np
import numpy.typing as npt
import yaml

from b210_sar.config import RunConfig, load_config, parse_config
from b210_sar.positions import AntennaPositions

IQ_FILE = "iq.sc16"
BURSTS_FILE = "bursts.csv"
SIGMF_FILE = "iq.sigmf-meta"
REPLICA_FILE = "chirp_replica.cf32"
CLOCK_SYNC_FILE = "clock_sync.csv"  # written by capture: USRP time against host clocks
POSITIONS_FILE = "antenna_positions.csv"  # added afterward from the autonomy stack
CONFIG_FILE = "config.yaml"
LOG_FILE = "run_log.txt"
SUMMARY_FILE = "run_summary.json"
CHECKSUM_FILE = "checksums.sha256"

# Keep in the same order as the header written by the C++ dataset writer.
BURST_COLUMNS = (
    "index",
    "sweep",
    "step",
    "sample_offset",
    "num_samples",
    "usrp_ticks",
    "usrp_time_s",
    "tx_freq_hz",
    "rx_freq_hz",
    "lo_offset_hz",
    "tx_gain_db",
    "rx_gain_db",
    "valid",
    "error_flags",
)


# Structured dtype of one burst row; same fields and order as BURST_COLUMNS (and the HDF5 SarBurst stream).
BURST_DTYPE = np.dtype(
    [
        ("index", "<i8"),
        ("sweep", "<i8"),
        ("step", "<i8"),
        ("sample_offset", "<i8"),  # in complex samples
        ("num_samples", "<i8"),
        ("usrp_ticks", "<i8"),
        ("usrp_time_s", "<f8"),
        ("tx_freq_hz", "<f8"),
        ("rx_freq_hz", "<f8"),
        ("lo_offset_hz", "<f8"),
        ("tx_gain_db", "<f8"),
        ("rx_gain_db", "<f8"),
        ("valid", "?"),
        ("error_flags", "<i8"),
    ]
)
assert BURST_DTYPE.names == BURST_COLUMNS

CLOCK_SYNC_COLUMNS = ("usrp_ticks", "host_monotonic_ns", "host_realtime_ns", "round_trip_ns")
CLOCK_SYNC_DTYPE = np.dtype([(n, "<i8") for n in CLOCK_SYNC_COLUMNS])


@dataclass(frozen=True)
class Run:
    path: Path | None
    config: RunConfig | None
    bursts: npt.NDArray[np.void]  # structured array with BURST_COLUMNS
    iq: npt.NDArray[np.int16]  # memory-mapped (or in-memory for synthetic runs), interleaved I/Q
    replica: npt.NDArray[np.complex64] | None = None
    clock_sync: npt.NDArray[np.void] | None = None
    positions: AntennaPositions | None = None  # None until the autonomy stack output is added
    config_text: str = ""  # verbatim config.yaml, if known


# ---------------------------------------------------------------------------------------------
# Common sweep-stream model


@dataclass(frozen=True)
class Sweep:
    """One sweep: row k is step k. Missing steps are zero IQ, NaN freqs, valid False, ticks -1."""

    sweep: int  # the sweep index as stored in the data
    iq: npt.NDArray[np.complex64]  # [num_steps, num_samples], raw sc16 counts (no scaling)
    tx_freq_hz: npt.NDArray[np.float64]
    rx_freq_hz: npt.NDArray[np.float64]
    valid: npt.NDArray[np.bool_]  # False for a missing or flagged-invalid burst
    usrp_ticks: npt.NDArray[np.int64]
    present: npt.NDArray[np.bool_]  # False where the burst is absent from the data
    burst_index: npt.NDArray[np.int64]  # the burst `index`, -1 where absent

    @property
    def complete(self) -> bool:
        return bool(self.present.all())

    @property
    def missing_steps(self) -> list[int]:
        return [int(k) for k in np.flatnonzero(~self.present)]


@dataclass
class SweepReport:
    """What the data does not contain or marks invalid. Never empty-by-omission: all counted here."""

    num_bursts: int
    num_sweeps: int
    expected_steps: int
    config_known: bool
    iq_source: str  # "run_folder", "hdf5", "run_dir" (HDF5 without iq, followed to the run folder), "none"
    incomplete: dict[int, list[int]] = field(default_factory=dict)  # sweep -> missing steps
    invalid_bursts: list[int] = field(default_factory=list)  # burst index with valid == False
    error_flag_bursts: list[int] = field(default_factory=list)  # burst index with error_flags != 0
    duplicates: list[tuple[int, int]] = field(default_factory=list)  # (sweep, step) seen twice; first used
    unexpected: list[tuple[int, int]] = field(default_factory=list)  # step >= expected_steps; not in sweeps
    truncated: list[int] = field(default_factory=list)  # burst index whose IQ lies past the end of the data
    notes: list[str] = field(default_factory=list)

    @property
    def complete_sweeps(self) -> int:
        return self.num_sweeps - len(self.incomplete)

    @property
    def ok(self) -> bool:
        return not (self.incomplete or self.invalid_bursts or self.duplicates or self.unexpected
                    or self.truncated)

    def summary(self) -> str:
        return (f"{self.num_bursts} bursts, {self.num_sweeps} sweeps ({self.complete_sweeps} complete) of "
                f"{self.expected_steps} steps; {len(self.invalid_bursts)} invalid, "
                f"{len(self.error_flag_bursts)} with error flags, {len(self.duplicates)} duplicate, "
                f"{len(self.unexpected)} unexpected-step, {len(self.truncated)} truncated; iq: {self.iq_source}")


def _c64(raw: npt.NDArray[np.int16]) -> npt.NDArray[np.complex64]:
    a = np.asarray(raw, dtype=np.float32)
    out = np.empty(a.shape[0] // 2, dtype=np.complex64)
    out.real = a[0::2]
    out.imag = a[1::2]
    return out


class SweepSource:
    """Common reader interface. Subclasses supply `bursts`, `config`, `_raw(row)` and `_has_iq(row)`.

    `sweep(i)` and `iter_sweeps()` use the sweep index stored in the data (it need not start at 0).
    Only metadata is held in memory; IQ is read per burst from a memory map / HDF5 row on demand.
    """

    config: RunConfig | None
    bursts: npt.NDArray[np.void]
    clock_sync: npt.NDArray[np.void] | None
    iq_source: str

    def _finish_init(self, expected_steps: int | None = None, notes: list[str] | None = None) -> None:
        b = self.bursts
        if expected_steps is None:
            expected_steps = self.config.waveform.num_steps if self.config else (
                int(b["step"].max()) + 1 if len(b) else 0)
        self.expected_steps = expected_steps
        rep = SweepReport(len(b), 0, expected_steps, self.config is not None, self.iq_source,
                          notes=list(notes or []))
        self._rows: dict[int, dict[int, int]] = {}  # sweep -> step -> row
        for r in range(len(b)):
            sw, st = int(b["sweep"][r]), int(b["step"][r])
            if st < 0 or st >= expected_steps:
                rep.unexpected.append((sw, st))
                self._rows.setdefault(sw, {})
                continue
            steps = self._rows.setdefault(sw, {})
            if st in steps:
                rep.duplicates.append((sw, st))
                continue
            steps[st] = r
        rep.num_sweeps = len(self._rows)
        rep.invalid_bursts = [int(x) for x in b["index"][~b["valid"]]]
        rep.error_flag_bursts = [int(x) for x in b["index"][b["error_flags"] != 0]]
        self._trunc_rows: set[int] = set()
        for r in range(len(b)):
            if not self._has_iq(r):
                self._trunc_rows.add(r)
                rep.truncated.append(int(b["index"][r]))
        for sw, steps in self._rows.items():
            miss = [k for k in range(expected_steps) if k not in steps or steps[k] in self._trunc_rows]
            if miss:
                rep.incomplete[sw] = miss
        self._report = rep
        self._num_samples = int(b["num_samples"][0]) if len(b) else 0

    # -- subclass hooks
    def _raw(self, row: int) -> npt.NDArray[np.int16]:
        raise NotImplementedError

    def _has_iq(self, row: int) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self) -> "SweepSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- public
    def report(self) -> SweepReport:
        return self._report

    @property
    def sweep_ids(self) -> list[int]:
        return sorted(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def sweep(self, i: int) -> Sweep:
        """Return the sweep with stored index `i` as `[num_steps, num_samples]` complex64 plus metadata."""
        if i not in self._rows:
            raise KeyError(f"no sweep {i} (have {len(self._rows)} sweeps)")
        steps = self._rows[i]
        n_steps, b = self._report.expected_steps, self.bursts
        present = np.zeros(n_steps, bool)
        got = {}
        for k, r in steps.items():
            if r in self._trunc_rows:
                continue
            present[k] = True
            got[k] = r
        ns = {int(b["num_samples"][r]) for r in got.values()}
        if len(ns) > 1:
            raise ValueError(f"sweep {i} has bursts of different lengths {sorted(ns)}")
        n = ns.pop() if ns else self._num_samples
        iq = np.zeros((n_steps, n), np.complex64)
        txf = np.full(n_steps, np.nan)
        rxf = np.full(n_steps, np.nan)
        valid = np.zeros(n_steps, bool)
        ticks = np.full(n_steps, -1, np.int64)
        bidx = np.full(n_steps, -1, np.int64)
        for k, r in got.items():
            iq[k] = _c64(self._raw(r))
            txf[k], rxf[k] = b["tx_freq_hz"][r], b["rx_freq_hz"][r]
            valid[k], ticks[k], bidx[k] = b["valid"][r], b["usrp_ticks"][r], b["index"][r]
        return Sweep(i, iq, txf, rxf, valid, ticks, present, bidx)

    def iter_sweeps(self) -> Iterator[Sweep]:
        for i in self.sweep_ids:
            yield self.sweep(i)

    def burst_iq(self, row: int) -> npt.NDArray[np.complex64]:
        """One burst by row position in `bursts`, as complex64."""
        if not self._has_iq(row):
            raise IndexError(f"burst row {row}: IQ not available")
        return _c64(self._raw(row))


def _read_csv_struct(path: Path, dtype: np.dtype) -> npt.NDArray[np.void]:
    with open(path, newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if header is None or tuple(h.strip() for h in header) != dtype.names:
            raise ValueError(f"{path}: header {header} does not match {dtype.names}")
        rows = [tuple(r) for r in rd if r]
    out = np.zeros(len(rows), dtype)
    for k, name in enumerate(dtype.names):
        col = [r[k] for r in rows]
        out[name] = np.array(col, dtype=np.float64).astype(dtype[name]) if dtype[name].kind == "i" else (
            np.array(col, dtype=np.float64) != 0 if dtype[name].kind == "b" else np.array(col, dtype=np.float64))
    return out


class RunFolderSource(SweepSource):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not (self.path / BURSTS_FILE).is_file():
            raise FileNotFoundError(f"{self.path}: not a run folder (no {BURSTS_FILE})")
        cfg_path = self.path / CONFIG_FILE
        self.config_text = cfg_path.read_text() if cfg_path.is_file() else ""
        self.config = load_config(cfg_path) if cfg_path.is_file() else None
        self.bursts = _read_csv_struct(self.path / BURSTS_FILE, BURST_DTYPE)
        cs = self.path / CLOCK_SYNC_FILE
        self.clock_sync = _read_csv_struct(cs, CLOCK_SYNC_DTYPE) if cs.is_file() else None
        rp = self.path / REPLICA_FILE
        self.replica = np.fromfile(rp, dtype=np.complex64) if rp.is_file() else None
        iq = self.path / IQ_FILE
        self.iq = (np.memmap(iq, dtype="<i2", mode="r") if iq.is_file() and iq.stat().st_size
                   else np.empty(0, np.int16))
        self.iq_source = "run_folder" if iq.is_file() else "none"
        self._finish_init(notes=[] if iq.is_file() else [f"{IQ_FILE} missing"])

    def _has_iq(self, row: int) -> bool:
        off, n = int(self.bursts["sample_offset"][row]), int(self.bursts["num_samples"][row])
        return off >= 0 and 2 * (off + n) <= len(self.iq)

    def _raw(self, row: int) -> npt.NDArray[np.int16]:
        off, n = int(self.bursts["sample_offset"][row]), int(self.bursts["num_samples"][row])
        return self.iq[2 * off:2 * (off + n)]

    def close(self) -> None:
        mm = getattr(self.iq, "_mmap", None)
        if mm is not None:
            self.iq = np.empty(0, np.int16)

    def run(self) -> Run:
        return Run(self.path, self.config, self.bursts, self.iq, self.replica, self.clock_sync,
                   None, self.config_text)


def load_run(path: str | Path) -> Run:
    """Open a run folder, memory-mapping the IQ data."""
    return RunFolderSource(path).run()


def burst_iq(run: Run, index: int) -> npt.NDArray[np.complex64]:
    """Return one burst as complex samples; `index` is the burst `index` column value."""
    rows = np.flatnonzero(run.bursts["index"] == index)
    if not len(rows):
        raise KeyError(f"no burst with index {index}")
    r = int(rows[0])
    off, n = int(run.bursts["sample_offset"][r]), int(run.bursts["num_samples"][r])
    if 2 * (off + n) > len(run.iq):
        raise IndexError(f"burst {index}: IQ lies past the end of the data")
    return _c64(run.iq[2 * off:2 * (off + n)])


def open_source(path: str | Path, stream: str | None = None) -> SweepSource:
    """Open a run folder (directory) or a console HDF5 recording (file) as a sweep source.

    `stream` selects the SarBurst group of an HDF5 file that has more than one (e.g. `b210_bursts`).
    """
    p = Path(path)
    if p.is_dir():
        return RunFolderSource(p)
    if p.is_file():
        from b210_sar.h5 import H5Source  # needs h5py (the `gui` extra)

        return H5Source(p, stream=stream)
    raise FileNotFoundError(path)


def write_run(path: str | Path, run: Run) -> None:
    """Write a run folder in the capture layout (iq.sc16, bursts.csv, config.yaml, clock_sync.csv,
    chirp_replica.cf32, iq.sigmf-meta). Used by the simulator and test fixtures; no checksums."""
    d = Path(path)
    d.mkdir(parents=True, exist_ok=True)
    np.asarray(run.iq, dtype="<i2").tofile(d / IQ_FILE)
    with open(d / BURSTS_FILE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(BURST_COLUMNS)
        for r in run.bursts:
            w.writerow([int(r[c]) if run.bursts.dtype[c].kind in "ib" else repr(float(r[c]))
                        for c in BURST_COLUMNS])
    text = run.config_text
    if not text and run.config is not None:
        text = yaml.safe_dump(dataclasses.asdict(run.config), sort_keys=False)
    if text:
        (d / CONFIG_FILE).write_text(text)
    if run.clock_sync is not None:
        with open(d / CLOCK_SYNC_FILE, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(CLOCK_SYNC_COLUMNS)
            for r in run.clock_sync:
                w.writerow([int(r[c]) for c in CLOCK_SYNC_COLUMNS])
    if run.replica is not None:
        np.asarray(run.replica, dtype=np.complex64).tofile(d / REPLICA_FILE)
    glob = {"core:datatype": "ci16_le", "core:version": "1.0.0"}
    if run.config is not None:
        glob["core:sample_rate"] = int(run.config.waveform.sample_rate_hz)
    (d / SIGMF_FILE).write_text(json.dumps({"global": glob, "captures": [], "annotations": []}, indent=2))


def verify_checksums(path: str | Path) -> list[str]:
    """Return the names of files whose checksum does not match."""
    raise NotImplementedError("T10")
