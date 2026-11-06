"""T7: pipeline stage 6, per-pulse bistatic backprojection on a ground grid.

Each pulse i adds s_i(R) exp(+j 2 pi f_i R / c), with R = |p_tx,i - x| + |p_rx,i - x|.
Sub-bands combine only in the image.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from b210_sar.positions import AntennaPositions

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ImageGrid:
    """Ground-plane grid. Spacing should be under half the range and azimuth resolution."""

    x_m: FloatArray
    y_m: FloatArray
    z_m: float = 0.0


def backproject(
    profiles: npt.NDArray[np.complex64],
    range_axis_m: FloatArray,
    center_freqs_hz: FloatArray,
    positions: AntennaPositions,
    grid: ImageGrid,
    use_gpu: bool = False,
) -> npt.NDArray[np.complex64]:
    """Form a complex image. use_gpu selects CuPy in place of NumPy."""
    raise NotImplementedError("T7")
