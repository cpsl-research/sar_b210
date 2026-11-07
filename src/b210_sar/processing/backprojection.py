"""T7: pipeline stage 6, per-burst bistatic backprojection on a ground grid.

Each burst i is its own pulse at its own position (docs/design_notes.md): one step's calibrated
spectrum Z_i (`Processor.calibrate`: leakage-normalised, tau_L = tx_rx_separation_m / c applied), with
only the bins that step keeps in the stitch placed on the common grid (`Processor.step_profiles`), gives
a complex profile s_i(r) on r = c tau / 2. The image is

    I(x) = sum_i  s_i(R_i(x) / 2) * exp(+j 2 pi f_ref R_i(x) / c),   R_i(x) = |p_tx,i - x| + |p_rx,i - x|,

over valid bursts, with f_ref = `Processor.reference_freq_hz`, the absolute RF of the common grid's
zero frequency (f_c when (N-1) Delta_f / delta_f is even), the same for every step.

Derivation of sign and f_ref. calibration.py's model: after leakage division and the tau_L term, a point
at two-way delay tau gives Z_k(f) = A exp(-j 2 pi nu tau), nu = f_k + f the absolute RF (up to one
constant phase exp(+j 2 pi f_c tau_L) common to every burst). Step k's bin n sits at grid bin
g = n + c_k with f_k = f_ref + c_k delta_f, so nu = f_ref + g delta_f and the profile (an IFFT over g)
is s(r) = A exp(-j 2 pi f_ref tau) sum_g W_g exp(+j 2 pi g delta_f (2 r / c - tau)). At the true pixel,
r = R_i / 2 = c tau / 2: the sum is real and positive (W_g >= 0) and the phase is -2 pi f_ref R_i / c,
which the +j term cancels for every burst and every step; elsewhere the phases do not align. Using
the step centre f_k instead of f_ref would leave a residual 2 pi (f_k - f_ref) R_i / c (+-19 MHz:
+-0.48 rad per metre of R) that differs between the steps. tests/test_backprojection.py checks it:
the wrong sign or f_k defocuses the image.

s_i is sampled by linear interpolation; build the Processor with pad >= 16 (bins c/(2 delta_f n_fft),
~0.09 m for 2 x 40 MHz), so the interpolation loss at the band edge stays under 0.1 dB.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from b210_sar.positions import AntennaPositions

FloatArray = npt.NDArray[np.float64]
SPEED_OF_LIGHT = 299792458.0


@dataclass(frozen=True)
class ImageGrid:
    """Ground-plane grid. Spacing should be under half the range and azimuth resolution.

    Images are indexed [iy, ix]: rows along y_m, columns along x_m.
    """

    x_m: FloatArray
    y_m: FloatArray
    z_m: float = 0.0

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.y_m), len(self.x_m))

    def points(self) -> FloatArray:
        """(num_pixels, 3) pixel centres in image order (row-major over y, x)."""
        yy, xx = np.meshgrid(np.asarray(self.y_m, float), np.asarray(self.x_m, float), indexing="ij")
        return np.stack([xx.ravel(), yy.ravel(), np.full(xx.size, float(self.z_m))], axis=1)


def _sample(profiles: np.ndarray, r0: float, dr: float, r: np.ndarray) -> np.ndarray:
    """Linear interpolation of each row of `profiles` (uniform axis r0 + n dr) at r (num_bursts, P)."""
    nb, nr = profiles.shape
    u = (r - r0) / dr
    i0 = np.floor(u).astype(np.int64)
    w = u - i0
    ok = (i0 >= 0) & (i0 < nr - 1)
    i0 = np.clip(i0, 0, nr - 2)
    flat = profiles.reshape(-1)
    base = (np.arange(nb, dtype=np.int64) * nr)[:, None]
    v = flat[base + i0] * (1 - w) + flat[base + i0 + 1] * w
    return np.where(ok, v, 0)


def _check(profiles, range_axis_m, positions):
    p = np.asarray(profiles)
    r = np.asarray(range_axis_m, float)
    if p.ndim != 2 or p.shape[1] != r.size:
        raise ValueError(f"profiles {p.shape} do not match the range axis ({r.size},)")
    if positions.tx.shape != (p.shape[0], 3) or positions.rx.shape != (p.shape[0], 3):
        raise ValueError(f"positions {positions.tx.shape}/{positions.rx.shape} do not match {p.shape[0]} bursts")
    dr = np.diff(r)
    if r.size < 2 or not np.allclose(dr, dr[0], rtol=1e-6, atol=1e-9):
        raise ValueError("range axis must be uniform")
    return p, float(r[0]), float(dr[0])


def bistatic_range(positions: AntennaPositions, points: np.ndarray) -> np.ndarray:
    """R_i(x) = |p_tx,i - x| + |p_rx,i - x|, (num_bursts, num_points)."""
    x = np.asarray(points, float).reshape(-1, 3)
    return (np.linalg.norm(positions.tx[:, None, :] - x[None], axis=-1)
            + np.linalg.norm(positions.rx[:, None, :] - x[None], axis=-1))


def contributions(
    profiles: np.ndarray,
    range_axis_m: FloatArray,
    ref_freq_hz: float,
    positions: AntennaPositions,
    points: np.ndarray,
) -> np.ndarray:
    """Each burst's term s_i(R_i/2) exp(+j 2 pi f_ref R_i / c) at `points`, (num_bursts, num_points)."""
    p, r0, dr = _check(profiles, range_axis_m, positions)
    big_r = bistatic_range(positions, points)
    return _sample(p, r0, dr, big_r / 2) * np.exp(2j * np.pi * ((ref_freq_hz * big_r / SPEED_OF_LIGHT) % 1.0))


def backproject(
    profiles: npt.NDArray[np.complex64],
    range_axis_m: FloatArray,
    ref_freq_hz: float,
    positions: AntennaPositions,
    grid: ImageGrid,
    weights: FloatArray | None = None,
    chunk_elements: int = 4_000_000,
    use_gpu: bool = False,
) -> npt.NDArray[np.complex128]:
    """Form a complex image (grid.shape) from per-burst profiles (num_bursts, len(range_axis_m)).

    `weights` (num_bursts,) multiplies each burst (0 for invalid bursts; complex values apply a phase
    correction, e.g. autofocus). Pixels are processed in chunks of about chunk_elements / num_bursts.
    """
    if use_gpu:
        raise NotImplementedError("GPU backprojection is not implemented (b210-31 out of scope)")
    p, _, _ = _check(profiles, range_axis_m, positions)
    if weights is not None:
        w = np.asarray(weights)
        if w.shape != (p.shape[0],):
            raise ValueError(f"weights has shape {w.shape}, need ({p.shape[0]},)")
        p = p * w[:, None]
        keep = w != 0
        p = p[keep]
        positions = AntennaPositions(positions.tx[keep], positions.rx[keep])
    pts = grid.points()
    out = np.zeros(pts.shape[0], dtype=complex)
    if p.shape[0] == 0:
        return out.reshape(grid.shape)
    step = max(1, int(chunk_elements) // p.shape[0])
    for a in range(0, pts.shape[0], step):
        out[a:a + step] = contributions(p, range_axis_m, ref_freq_hz, positions, pts[a:a + step]).sum(axis=0)
    return out.reshape(grid.shape)
