"""T6: pipeline stages 2-3, chirp averaging and range compression."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex64]


def average_chirps(burst: ComplexArray, chirp_samples: int) -> ComplexArray:
    """Coherently average the chirps of a dwell into one pulse."""
    raise NotImplementedError("T6")


def range_compress(pulse: ComplexArray, replica: ComplexArray, upsample: int = 4) -> ComplexArray:
    """Windowed circular matched filter against the replica, upsampled 4-8x."""
    raise NotImplementedError("T6")
