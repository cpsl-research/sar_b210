"""T8: pipeline stage 7, removal of residual position and timing error."""

from __future__ import annotations

from typing import Callable

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex64]
FloatArray = npt.NDArray[np.float64]


def phase_gradient_autofocus(image: ComplexArray, iterations: int = 5) -> ComplexArray:
    """Phase gradient autofocus on a formed image."""
    raise NotImplementedError("T8")


def sharpness_autofocus(
    form_image: Callable[[FloatArray], ComplexArray], num_pulses: int
) -> FloatArray:
    """Find the per-pulse phase correction that maximizes image sharpness."""
    raise NotImplementedError("T8")
