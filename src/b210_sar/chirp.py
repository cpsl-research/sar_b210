"""Tapered baseband chirp for the simulator. Real runs use the replica written by capture/src/chirp.cpp."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from b210_sar.config import WaveformConfig


def generate_chirp(waveform: WaveformConfig) -> np.ndarray:
    """Return one period of the linear FM chirp as complex64, with raised-cosine edge taper."""
    raise NotImplementedError("T2")


def write_replica(chirp: np.ndarray, path: str | Path) -> None:
    """Write the exact transmitted chirp as chirp_replica.cf32."""
    raise NotImplementedError("T2")


def main() -> int:
    """CLI: sar-chirp CONFIG OUTPUT."""
    raise NotImplementedError("T2")
