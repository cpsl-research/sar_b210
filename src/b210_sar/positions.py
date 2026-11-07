"""T5: per-burst TX and RX antenna positions, resolved by the autonomy stack.

This project does no IMU handling, attitude or lever-arm work. The autonomy stack matches its
trajectory to burst times from bursts.csv and clock_sync.csv, and supplies antenna phase-center
positions for each burst in a local Cartesian frame, in metres.

File (`antenna_positions.csv`): a header row with POSITION_COLUMNS, then one row per burst; `index` is
the burst `index` column of bursts.csv. sar_b210 only checks the file against the run: every requested
burst has a row, values are finite, and the median TX-RX distance matches the config's
`antenna.tx_rx_separation_m` (which sets tau_L) within 1 cm, so the two cannot silently disagree.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

# Provisional until the autonomy stack's output format is known.
POSITION_COLUMNS = ("index", "tx_x", "tx_y", "tx_z", "rx_x", "rx_y", "rx_z")
SEPARATION_TOLERANCE_M = 0.01


class PositionsError(ValueError):
    """The positions file does not match the run or its config."""


@dataclass(frozen=True)
class AntennaPositions:
    tx: npt.NDArray[np.float64]  # shape (num_bursts, 3), metres
    rx: npt.NDArray[np.float64]  # shape (num_bursts, 3), metres


def load_positions(
    path: str | Path,
    burst_indices: npt.NDArray[np.int_],
    tx_rx_separation_m: float,
    tolerance_m: float = SEPARATION_TOLERANCE_M,
) -> AntennaPositions:
    """Load positions for `burst_indices` (in that order); require one finite row for each.

    Raises PositionsError on a wrong header, a missing or duplicated burst row, a non-finite value, or a
    median |tx - rx| that differs from `tx_rx_separation_m` (antenna.tx_rx_separation_m) by more than
    `tolerance_m`.
    """
    p = Path(path)
    with open(p, newline="") as f:
        rows = list(csv.reader(f))
    if not rows or tuple(c.strip() for c in rows[0]) != POSITION_COLUMNS:
        raise PositionsError(f"{p}: header must be {','.join(POSITION_COLUMNS)}")
    try:
        data = np.array([[float(v) for v in r] for r in rows[1:] if r], dtype=float).reshape(-1, len(POSITION_COLUMNS))
    except ValueError as e:
        raise PositionsError(f"{p}: {e}") from e
    idx = data[:, 0]
    if np.any(idx != np.round(idx)):
        raise PositionsError(f"{p}: non-integer burst index")
    idx = idx.astype(np.int64)
    uniq, counts = np.unique(idx, return_counts=True)
    if np.any(counts > 1):
        raise PositionsError(f"{p}: duplicate rows for bursts {uniq[counts > 1][:10].tolist()}")
    want = np.asarray(burst_indices, dtype=np.int64).reshape(-1)
    row_of = dict(zip(idx.tolist(), range(idx.size)))
    missing = [int(i) for i in want if int(i) not in row_of]
    if missing:
        raise PositionsError(f"{p}: no row for {len(missing)} of {want.size} bursts (first: {missing[:10]})")
    sel = data[[row_of[int(i)] for i in want], 1:]
    if not np.all(np.isfinite(sel)):
        raise PositionsError(f"{p}: non-finite positions")
    tx, rx = sel[:, :3].copy(), sel[:, 3:].copy()
    if want.size:
        sep = float(np.median(np.linalg.norm(tx - rx, axis=1)))
        if abs(sep - tx_rx_separation_m) > tolerance_m:
            raise PositionsError(
                f"{p}: median |tx - rx| is {sep:.4f} m but antenna.tx_rx_separation_m is "
                f"{tx_rx_separation_m:.4f} m (tolerance {tolerance_m:g} m); fix the config or the positions")
    return AntennaPositions(tx=tx, rx=rx)


def write_positions(path: str | Path, burst_indices: npt.NDArray[np.int_], tx: np.ndarray, rx: np.ndarray) -> None:
    """Write `antenna_positions.csv` (simulator and tests; real files come from the autonomy stack)."""
    idx = np.asarray(burst_indices, dtype=np.int64).reshape(-1)
    tx, rx = np.asarray(tx, float).reshape(-1, 3), np.asarray(rx, float).reshape(-1, 3)
    if not (idx.size == tx.shape[0] == rx.shape[0]):
        raise ValueError("burst_indices, tx and rx need the same number of rows")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(POSITION_COLUMNS)
        for i, a, b in zip(idx, tx, rx):
            w.writerow([int(i)] + [repr(float(v)) for v in (*a, *b)])


def estimate_time_offset(
    positions: AntennaPositions,
    radar_activity: npt.NDArray[np.float64],
    radar_times_s: npt.NDArray[np.float64],
) -> float:
    """Stage 4 check: compare motion start in the positions with the radar static-clutter change."""
    raise NotImplementedError("T5")
