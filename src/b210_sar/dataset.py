"""Run folder format, shared with capture/src/dataset_writer.cpp."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from b210_sar.config import RunConfig
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


@dataclass(frozen=True)
class Run:
    path: Path
    config: RunConfig
    bursts: npt.NDArray[np.void]  # structured array with BURST_COLUMNS
    iq: npt.NDArray[np.int16]  # memory-mapped, interleaved I/Q
    replica: npt.NDArray[np.complex64]
    clock_sync: npt.NDArray[np.void]
    positions: AntennaPositions | None  # None until the autonomy stack output is added


def load_run(path: str | Path) -> Run:
    """Open a run folder, memory-mapping the IQ data."""
    raise NotImplementedError("T6")


def burst_iq(run: Run, index: int) -> npt.NDArray[np.complex64]:
    """Return one burst as complex samples."""
    raise NotImplementedError("T6")


def write_run(path: str | Path, run: Run) -> None:
    """Write a run folder in the capture layout. Used by the simulator."""
    raise NotImplementedError("T9")


def verify_checksums(path: str | Path) -> list[str]:
    """Return the names of files whose checksum does not match."""
    raise NotImplementedError("T10")
