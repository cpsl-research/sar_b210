"""T5: per-burst TX and RX antenna positions, resolved by the autonomy stack.

This project does no IMU handling, attitude or lever-arm work. The autonomy stack matches its
trajectory to burst times from bursts.csv and clock_sync.csv, and supplies antenna phase-center
positions for each burst in a local Cartesian frame, in metres.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

# Provisional until the autonomy stack's output format is known.
POSITION_COLUMNS = ("index", "tx_x", "tx_y", "tx_z", "rx_x", "rx_y", "rx_z")


@dataclass(frozen=True)
class AntennaPositions:
    tx: npt.NDArray[np.float64]  # shape (num_bursts, 3), metres
    rx: npt.NDArray[np.float64]  # shape (num_bursts, 3), metres


def load_positions(path: str | Path, burst_indices: npt.NDArray[np.int_]) -> AntennaPositions:
    """Load positions and require one row for every requested burst."""
    raise NotImplementedError("T5")


def estimate_time_offset(
    positions: AntennaPositions,
    radar_activity: npt.NDArray[np.float64],
    radar_times_s: npt.NDArray[np.float64],
) -> float:
    """Stage 4 check: compare motion start in the positions with the radar static-clutter change."""
    raise NotImplementedError("T5")
