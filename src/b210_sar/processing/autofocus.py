"""T8: pipeline stage 7, removal of residual position and timing error.

Positions come from wheel odometry only (no IMU), so the residual along the pass (scale error, heading
drift, wheel slip) is left to autofocus. `prominent_point_autofocus` is the first method: per burst,
the phase of its contribution at the brightest pixel, minus a linear fit over burst time, is removed;
repeated until the correction stops changing (at most `iterations` times). The linear part is kept
because it only shifts the image (a constant phase, or an along-track displacement), which a single
point cannot tell from a target position. It assumes one dominant point scatterer (the corner reflector
of the first experiment); on extended scenes use phase gradient autofocus (not implemented).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import numpy.typing as npt

from b210_sar.positions import AntennaPositions
from b210_sar.processing.backprojection import ImageGrid, backproject, contributions

ComplexArray = npt.NDArray[np.complex64]
FloatArray = npt.NDArray[np.float64]


@dataclass
class AutofocusResult:
    image: np.ndarray  # complex, grid.shape, with the correction applied
    phase_rad: np.ndarray  # (num_bursts,) correction; the bursts were multiplied by exp(-j phase)
    peak_xy: tuple[float, float]  # brightest pixel of the final image
    iterations: int
    rms_update_rad: list[float] = field(default_factory=list)  # rms of each iteration's update


def prominent_point_autofocus(
    profiles: np.ndarray,
    range_axis_m: FloatArray,
    ref_freq_hz: float,
    positions: AntennaPositions,
    grid: ImageGrid,
    times_s: FloatArray | None = None,
    weights: FloatArray | None = None,
    iterations: int = 5,
    tol_rad: float = 0.01,
    image: np.ndarray | None = None,
) -> AutofocusResult:
    """Per-burst phase correction from the brightest pixel (see module doc).

    `profiles`, `positions` and `weights` as for `backproject` (zero weight: invalid burst, ignored).
    `times_s` (num_bursts,) orders the bursts for unwrapping and is the linear-fit variable (default:
    burst order). `image`, if given, is the uncorrected image (saves one backprojection).
    """
    p = np.asarray(profiles)
    nb = p.shape[0]
    w = np.ones(nb) if weights is None else np.asarray(weights, dtype=float)
    t = np.arange(nb, dtype=float) if times_s is None else np.asarray(times_s, dtype=float)
    use = np.flatnonzero(w != 0)
    if use.size < 3:
        raise ValueError("autofocus needs at least 3 valid bursts")
    order = use[np.argsort(t[use], kind="stable")]
    tt = t[order] - t[order].mean()
    phase = np.zeros(nb)
    img = backproject(p, range_axis_m, ref_freq_hz, positions, grid, weights=w) if image is None else image
    rms_hist: list[float] = []
    done = 0
    for done in range(1, iterations + 1):
        a = np.abs(img)
        iy, ix = np.unravel_index(int(np.argmax(a)), a.shape)
        x = np.array([[grid.x_m[ix], grid.y_m[iy], grid.z_m]])
        sub = AntennaPositions(positions.tx[order], positions.rx[order])
        c = contributions(p[order] * (w[order] * np.exp(-1j * phase[order]))[:, None], range_axis_m,
                          ref_freq_hz, sub, x)[:, 0]
        ph = np.unwrap(np.angle(c))
        resid = ph - np.polyval(np.polyfit(tt, ph, 1), tt)
        phase[order] += resid
        rms = float(np.sqrt(np.mean(resid ** 2)))
        rms_hist.append(rms)
        img = backproject(p, range_axis_m, ref_freq_hz, positions, grid, weights=w * np.exp(-1j * phase))
        if rms < tol_rad:
            break
    a = np.abs(img)
    iy, ix = np.unravel_index(int(np.argmax(a)), a.shape)
    return AutofocusResult(img, phase, (float(grid.x_m[ix]), float(grid.y_m[iy])), done, rms_hist)


def phase_gradient_autofocus(image: ComplexArray, iterations: int = 5) -> ComplexArray:
    """Phase gradient autofocus on a formed image."""
    raise NotImplementedError("T8")


def sharpness_autofocus(
    form_image: Callable[[FloatArray], ComplexArray], num_pulses: int
) -> FloatArray:
    """Find the per-pulse phase correction that maximizes image sharpness."""
    raise NotImplementedError("T8")
