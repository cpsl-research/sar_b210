"""T6: pipeline stages 4-5, per-step LO-phase removal by the leakage reference, static clutter removal.

Method: docs/delay_doppler.md section 3. numpy only. For each burst (sweep m, step k) with matched
spectrum Y(f) on the FFT grid f = n f_s / N_c:

    Z(f) = Y(f) / c * exp(+j 2 pi f tau_env) * exp(-j 2 pi (f_k - f_c + f) tau_L),
    c    = y(tau_env) = (1/N_c) sum_f Y(f) exp(+j 2 pi f tau_env)   (DC bin excluded),
    tau_env = tau_sys + tau_L.

The LO phase changes on every retune, so `c` is computed per burst; only the static terms live
in `LeakageCal`: tau_env (fitted once per run, fractional), tau_L (antenna separation / c or a
corner reflector) and phase_only.

Sign of the tau_L term (resolved in b210-13 Step 3). Under the physical baseband model
r(t) = x(t - tau) exp(-j 2 pi f_k tau) (UHD: RF above the LO is positive baseband frequency),
dividing by the leakage leaves exp(-j 2 pi nu (tau_i - tau_L)), nu = f_k + f, i.e. range measured
from the leakage. Multiplying by exp(-j 2 pi nu tau_L) restores the absolute delay tau_i. The
outline's exp(+j 2 pi f_k tau_leak) is the wrong sign under this model: applied over the full nu it
moves every target by -c tau_L (3 m path: 37.3 m -> 34.3 m, tests/test_delay_doppler.py test 4);
applied at f_k only it also breaks the step-to-step phase.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex64]
FloatArray = npt.NDArray[np.float64]

SPEED_OF_LIGHT = 299792458.0


@dataclass(frozen=True)
class LeakageCal:
    """Static leakage-calibration terms. `tau_env_s` None: no reference (RX-only run), c = 1."""

    tau_env_s: float | None = None
    tau_leak_s: float = 0.0
    phase_only: bool = False

    @property
    def enabled(self) -> bool:
        return self.tau_env_s is not None


def fft_freqs(num_samples: int, sample_rate_hz: float) -> FloatArray:
    return np.fft.fftfreq(num_samples, 1.0 / sample_rate_hz)


def find_leakage_window(profiles: ComplexArray, half_width: int = 8) -> slice:
    """Locate the leakage peak once from static data: the strongest mean |profile| sample +/- half_width.

    `profiles` is (num_bursts, n) compressed pulses (any upsampling). The window is then held fixed.
    """
    p = np.abs(np.asarray(profiles)).reshape(-1, np.shape(profiles)[-1]).mean(axis=0)
    i = int(np.argmax(p))
    return slice(max(0, i - half_width), min(p.size, i + half_width + 1))


def _power_at(spectra: np.ndarray, f: np.ndarray, tau: float) -> float:
    return float(np.sum(np.abs(spectra @ np.exp(2j * np.pi * f * tau)) ** 2))


def estimate_tau_env(
    spectra: np.ndarray,
    sample_rate_hz: float,
    search_s: tuple[float, float] | None = None,
    upsample: int = 8,
) -> float:
    """Fractional delay of the leakage peak, tau_env = tau_sys + tau_L, in seconds.

    `spectra` is (num_bursts, N_c) matched spectra Y (FFT order) of static data, any LO phases.
    Maximizes sum_m |y_m(tau)|^2: for the leakage alone |X|^2 is real and non-negative, so |y(tau)|
    peaks exactly at tau_env. Coarse search on an `upsample`x grid within `search_s` (default: whole
    period), then golden-section refinement to ~1e-6 sample. The DC bin is excluded.
    """
    y = np.atleast_2d(np.asarray(spectra, dtype=complex)).copy()
    n = y.shape[-1]
    y[:, 0] = 0
    f = fft_freqs(n, sample_rate_hz)
    m = n * upsample
    big = np.zeros((y.shape[0], m), dtype=complex)
    h = n // 2
    big[:, :h] = y[:, :h]
    big[:, m - (n - h) :] = y[:, h:]
    p = np.sum(np.abs(np.fft.ifft(big, axis=-1)) ** 2, axis=0)
    t_grid = np.arange(m) / (upsample * sample_rate_hz)
    if search_s is not None:
        sel = (t_grid >= search_s[0]) & (t_grid <= search_s[1])
        if not sel.any():
            raise ValueError(f"search window {search_s} holds no samples")
        p = np.where(sel, p, -np.inf)
    i = int(np.argmax(p))
    step = 1.0 / (upsample * sample_rate_hz)
    a, b = t_grid[i] - step, t_grid[i] + step
    g = (np.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    pc, pd = _power_at(y, f, c), _power_at(y, f, d)
    while (b - a) > 1e-6 / sample_rate_hz:
        if pc > pd:
            b, d, pd = d, c, pc
            c = b - g * (b - a)
            pc = _power_at(y, f, c)
        else:
            a, c, pc = c, d, pd
            d = a + g * (b - a)
            pd = _power_at(y, f, d)
    return float((a + b) / 2)


def leakage_reference(
    spectra: np.ndarray, tau_env_s: float, sample_rate_hz: float, reference_energy: float = 1.0
) -> np.ndarray:
    """c = y(tau_env) / reference_energy per burst: the compressed pulse at the fractional leakage peak.

    DC bin excluded. With reference_energy = sum_{f != 0} |X(f)|^2 / N_c, c is the leakage amplitude
    in replica units (times the LO phasor), so normalized targets read a_i / a_L.
    """
    y = np.asarray(spectra)
    n = y.shape[-1]
    e = np.exp(2j * np.pi * fft_freqs(n, sample_rate_hz) * tau_env_s)
    e[0] = 0
    return (y @ e) / (n * reference_energy)


def normalize_to_leakage(
    spectra: np.ndarray,
    tau_env_s: float,
    sample_rate_hz: float,
    phase_only: bool = False,
    reference_energy: float = 1.0,
) -> np.ndarray:
    """Divide each burst's matched spectrum by its leakage value c and move the leakage to zero delay.

    Returns Y / c * exp(+j 2 pi f tau_env). `phase_only` divides by c/|c| (keeps amplitude). A burst
    with c == 0 (missing IQ) returns zeros.
    """
    y = np.asarray(spectra)
    n = y.shape[-1]
    c = leakage_reference(y, tau_env_s, sample_rate_hz, reference_energy)
    mag = np.abs(c)
    if phase_only:
        c = np.where(mag > 0, c / np.where(mag > 0, mag, 1), 0)
        mag = np.abs(c)
    inv = np.where(mag > 0, 1 / np.where(mag > 0, c, 1), 0)
    ramp = np.exp(2j * np.pi * fft_freqs(n, sample_rate_hz) * tau_env_s)
    return y * inv[..., None] * ramp


def tau_leak_from_separation(separation_m: float) -> float:
    """tau_L = antenna separation (the leakage path) / c."""
    return float(separation_m) / SPEED_OF_LIGHT


def estimate_tau_leak(reflector_phases: FloatArray, step_freqs_hz: FloatArray) -> float:
    """Fit tau_L from the residual phase across steps on a surveyed corner reflector.

    `reflector_phases` are the reflector's phases (rad) per step, processed with tau_L = 0, minus
    the phase predicted for its surveyed range. With tau_L omitted the residual is +2 pi f_k tau_L
    (+ a constant), so tau_L = slope / (2 pi). Needs |Delta f * tau_L| < 1/2 (unwrapping).
    """
    f = np.asarray(step_freqs_hz, dtype=float)
    ph = np.unwrap(np.asarray(reflector_phases, dtype=float))
    if f.size < 2:
        raise ValueError("need at least two steps")
    slope = np.polyfit(f - f.mean(), ph, 1)[0]
    return float(slope / (2 * np.pi))


def apply_tau_leak(
    spectrum: np.ndarray, rf_offset_hz: float, tau_leak_s: float, sample_rate_hz: float
) -> np.ndarray:
    """Apply exp(-j 2 pi (f_k - f_c + f) tau_leak) to one step's leakage-normalized spectrum.

    `rf_offset_hz` is f_k - f_c; f runs over the FFT grid of the last axis. Sign: see module doc
    (the outline's +j sign shifts range by -c tau_leak).
    """
    z = np.asarray(spectrum)
    f = fft_freqs(z.shape[-1], sample_rate_hz)
    return z * np.exp(-2j * np.pi * (rf_offset_hz + f) * tau_leak_s)


def remove_static_clutter(spectra: np.ndarray, valid: npt.NDArray[np.bool_]) -> np.ndarray:
    """Subtract the mean calibrated spectrum per step across sweeps (display toggle, off by default).

    `spectra` is (num_sweeps, num_steps, N_c), `valid` (num_sweeps, num_steps); invalid bursts stay zero.
    """
    z = np.asarray(spectra)
    v = np.asarray(valid, dtype=bool)
    cnt = np.maximum(v.sum(axis=0), 1)[:, None]
    mean = (z * v[..., None]).sum(axis=0) / cnt
    return np.where(v[..., None], z - mean[None], 0)
