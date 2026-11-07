"""Range profiles and delay-Doppler maps from stitched stepped chirps (numpy only).

Method: docs/delay_doppler.md. Per burst (sweep m, step k): average the dwell's chirps, matched
filter on the 50 kHz FFT grid (Y), remove the LO phase with the leakage reference and align the
envelope (Z, calibration.py). Steps are stitched by frequency-domain concatenation: step k's bin n
goes to grid bin g = n + k * Delta_f / delta_f + g0; bins with |X|^2 below -6 dB are dropped, the
overlap is split at the midpoint between step centres, kept bins are equalized by 1/|X|^2, one
window spans the whole support, and the IFFT is zero-padded >= `pad`x.

DC bin (deviation from the memo's "zeroed", b210-13 Step 3): each step's DC bin value is never used
(DC offset / LO leakage, random phase), but by default it is replaced by the mean of its two
equalized neighbours instead of left as a hole. A zeroed bin is a missing spectral line: it leaves
a flat sidelobe floor ~-55 dB (Hann) below the strongest return, i.e. below the leakage, which caps a
target 20 dB under the leakage at PSL -29 dB. Interpolated, that floor is < -75 dB at 25-50 m; the
neighbours' phase differs by 2 pi delta_f tau (0.08 rad at 37 m), so the fill is exact for R << R_amb.
`dc="zero"` keeps the memo's behaviour.

Delay-Doppler: Doppler is transformed per step first, at each burst's own timestamp,
D_k(f_D) = sum_m w_m Z_mk exp(-j 2 pi f_D (t_mk - t_mid)), f_D = j / (M T_s), then each Doppler
row is stitched (section 6). T_s is the mean sweep period of the CPI from the timestamps.

Profiles are scaled so a point target with equalized spectrum a * exp(...) peaks at |a| (with the
leakage reference: a_i / a_L; RX-only: received amplitude in sc16 counts per unit replica).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from b210_sar.chirp import chirp_samples, generate_chirp
from b210_sar.config import RunConfig, step_centres
from b210_sar.dataset import Sweep
from b210_sar.processing.calibration import (
    LeakageCal,
    apply_tau_leak,
    estimate_tau_env,
    normalize_to_leakage,
)
from b210_sar.processing.pulse import average_chirps, matched_spectrum

SPEED_OF_LIGHT = 299792458.0


def _window(name: str | np.ndarray | None, n: int) -> np.ndarray:
    if name is None or (isinstance(name, str) and name in ("rect", "none")):
        return np.ones(n)
    if isinstance(name, str):
        if name == "hann":
            return np.hanning(n)
        if name == "hamming":
            return np.hamming(n)
        raise ValueError(f"unknown window {name!r} (hann, hamming, rect)")
    w = np.asarray(name, dtype=float)
    if w.shape != (n,):
        raise ValueError(f"window has shape {w.shape}, need ({n},)")
    return w


@dataclass(frozen=True)
class SweepSpectra:
    """Calibrated per-step spectra of one sweep: the cacheable input of `delay_doppler_map`."""

    sweep: int
    spectra: np.ndarray  # (num_steps, N_c) complex, FFT order; zero where not valid
    times_s: np.ndarray  # (num_steps,) burst USRP time, NaN where absent
    valid: np.ndarray  # (num_steps,) bool


@dataclass(frozen=True)
class RangeProfile:
    ranges_m: np.ndarray
    profile: np.ndarray  # complex


@dataclass(frozen=True)
class DelayDopplerMap:
    ranges_m: np.ndarray  # (num_ranges,)
    doppler_hz: np.ndarray  # (num_doppler,), ascending, zero Doppler at index num_doppler // 2
    velocity_mps: np.ndarray  # closing speed, f_D * lambda / 2
    map: np.ndarray  # (num_doppler, num_ranges) complex
    sweep_period_s: float  # mean T_s of the CPI from the timestamps
    t_mid_s: float
    sweeps: np.ndarray  # stored sweep indices used
    missing_bursts: int
    wavelength_m: float

    @property
    def num_sweeps(self) -> int:
        return int(self.sweeps.size)

    @property
    def doppler_resolution_hz(self) -> float:
        return 1.0 / (self.num_sweeps * self.sweep_period_s)

    @property
    def velocity_resolution_mps(self) -> float:
        return self.wavelength_m / (2 * self.num_sweeps * self.sweep_period_s)

    @property
    def velocity_span_mps(self) -> float:
        return self.wavelength_m / (4 * self.sweep_period_s)

    @property
    def zero_doppler_index(self) -> int:
        return int(np.flatnonzero(self.doppler_hz == 0)[0])

    def power_db(self, floor: float = 1e-12) -> np.ndarray:
        return 20 * np.log10(np.abs(self.map) + floor)


@dataclass
class Processor:
    """Precomputed geometry for one config. Construct once per run; cheap to call per sweep.

    `steps` selects a subset of steps to stitch (None: all; [0]: one 45 MHz step). `cal` None or
    LeakageCal() (tau_env None) processes without a leakage reference (RX-only data). `range_window`
    "hann" (default), "hamming", "rect" or an array over the stitched support. `pad` >= 4 zero-pad
    factor of the range IFFT. Range axis kept: [min_range_m, max_range_m]. `dc` "interpolate"
    (default) or "zero" (see module doc).
    """

    config: RunConfig
    replica: np.ndarray | None = None
    cal: LeakageCal | None = None
    steps: Sequence[int] | None = None
    range_window: str | np.ndarray | None = "hann"
    pad: int = 4
    min_range_m: float = -20.0
    max_range_m: float | None = None
    keep_db: float = -6.0
    dc: str = "interpolate"
    _g: list = field(init=False, repr=False)

    def __post_init__(self) -> None:
        w = self.config.waveform
        self.sample_rate_hz = float(w.sample_rate_hz)
        self.num_chirp_samples = chirp_samples(w)
        if self.replica is None:
            self.replica = generate_chirp(w)
        self.replica = np.asarray(self.replica, dtype=np.complex64)
        if self.replica.size != self.num_chirp_samples:
            raise ValueError(f"replica has {self.replica.size} samples, config says {self.num_chirp_samples}")
        nc, fs = self.num_chirp_samples, self.sample_rate_hz
        self.bin_hz = fs / nc
        self.num_steps = int(w.num_steps)
        self.center_freq_hz = float(self.config.rf.center_freq_hz)
        self.wavelength_m = SPEED_OF_LIGHT / self.center_freq_hz
        self.step_offsets_hz = np.array(step_centres(self.config)) - self.center_freq_hz
        d = w.step_spacing_hz / self.bin_hz
        if self.num_steps > 1 and abs(d - round(d)) > 1e-6:
            raise ValueError(
                f"step spacing {w.step_spacing_hz:g} Hz is not a multiple of the FFT bin "
                f"f_s/N_c = {self.bin_hz:g} Hz (ratio {d:.4f}); stitching needs an integer ratio")
        d = int(round(d))
        steps = list(range(self.num_steps)) if self.steps is None else sorted(int(k) for k in self.steps)
        if not steps or min(steps) < 0 or max(steps) >= self.num_steps:
            raise ValueError(f"steps {self.steps} outside 0..{self.num_steps - 1}")
        self.active_steps = steps

        X = np.fft.fft(self.replica.astype(np.complex128))
        self.x2 = np.abs(X) ** 2
        kb = np.rint(np.fft.fftfreq(nc) * nc).astype(int)  # signed bin index, FFT order
        g0 = -((self.num_steps - 1) * d // 2)  # grid bin of f_c (exact for even (N-1)*d)
        self.grid_offset_hz = ((self.num_steps - 1) * d / 2 + g0) * self.bin_hz  # 0 or half a bin
        centre = {k: k * d + g0 for k in steps}  # grid bin of each step centre
        # Absolute RF of signed grid bin 0 (the IFFT's zero frequency): step k's bin 0 is f_k, at grid bin
        # centre[k]. Equals f_c when (N-1) d is even. Per-step profiles and backprojection use it.
        k0 = steps[0]
        self.reference_freq_hz = float(step_centres(self.config)[k0] - centre[k0] * self.bin_hz)
        if self.dc not in ("interpolate", "zero"):
            raise ValueError(f"dc must be 'interpolate' or 'zero', not {self.dc!r}")
        strong = self.x2 > 10 ** (self.keep_db / 10) * self.x2.max()
        sel, dcs = [], []
        for j, k in enumerate(steps):
            g = kb + centre[k]
            keep = strong.copy()
            if j > 0:
                keep &= g >= (centre[steps[j - 1]] + centre[k]) / 2
            if j < len(steps) - 1:
                keep &= g < (centre[k] + centre[steps[j + 1]]) / 2
            if keep[0] and self.dc == "interpolate" and keep[1] and keep[nc - 1]:
                dcs.append((k, int(centre[k])))
            keep[0] = False  # the DC bin's own value is never used (DC offset, random phase)
            src = np.flatnonzero(keep)
            sel.append((k, src, g[src]))
        allg = np.concatenate([s[2] for s in sel] + [np.array([g for _, g in dcs], dtype=int)])
        if allg.size == 0:
            raise ValueError("no bins kept")
        self.grid_min, self.grid_max = int(allg.min()), int(allg.max())
        n_grid = self.grid_max - self.grid_min + 1
        # Grid bins no step keeps. Each step's DC bin is excluded (interpolated, or deliberately zeroed by
        # dc="zero"), and so is a single isolated bin: the chirp-edge spectrum ripples about the keep threshold
        # at the split midpoint, which costs one 50 kHz bin (2 x 40 MHz at 38 MHz spacing) and is not a gap.
        kept = np.zeros(n_grid, dtype=bool)
        kept[np.union1d(allg, [int(centre[k]) for k in steps]) - self.grid_min] = True
        miss = np.flatnonzero(~kept)
        runs = np.split(miss, np.flatnonzero(np.diff(miss) > 1) + 1) if miss.size else []
        n_holes = sum(r.size for r in runs if r.size > 1)
        if n_holes:
            warnings.warn(f"stitched spectrum has holes: {n_holes} bins ({n_holes * self.bin_hz / 1e6:.2f} MHz) of "
                          f"{n_grid * self.bin_hz / 1e6:.2f} MHz are kept by no step (step spacing "
                          f"{w.step_spacing_hz:g} Hz vs chirp bandwidth {w.chirp_bandwidth_hz:g} Hz); "
                          "expect grating lobes at multiples of c/(2 delta_f)", stacklevel=2)
        win = _window(self.range_window, n_grid)
        norm = win[allg - self.grid_min].sum()
        self.stitched_bandwidth_hz = n_grid * self.bin_hz
        self.n_grid = n_grid
        self._g = [(k, src, gg - self.grid_min, win[gg - self.grid_min] / self.x2[src] / norm)
                   for k, src, gg in sel]
        # DC bin of each step: mean of its equalized neighbours (bins -1, +1), windowed
        self._dc = [(k, g - self.grid_min, 0.5 * win[g - self.grid_min] / norm / self.x2[nc - 1],
                     0.5 * win[g - self.grid_min] / norm / self.x2[1]) for k, g in dcs]
        self.grid_freqs_hz = (np.arange(self.grid_min, self.grid_max + 1)) * self.bin_hz + self.grid_offset_hz

        n_fft = 1
        while n_fft < self.pad * n_grid:
            n_fft *= 2
        self.n_fft = n_fft
        self.max_unambiguous_range_m = SPEED_OF_LIGHT / (2 * self.bin_hz)
        self.range_bin_m = self.max_unambiguous_range_m / n_fft
        r = np.arange(n_fft) * self.range_bin_m
        r = np.where(r >= self.max_unambiguous_range_m + self.min_range_m, r - self.max_unambiguous_range_m, r)
        order = np.argsort(r, kind="stable")
        r = r[order]
        keep_r = r >= self.min_range_m
        if self.max_range_m is not None:
            keep_r &= r <= self.max_range_m
        self._ridx = order[keep_r]
        self.ranges_m = r[keep_r]
        self._place = (self.grid_min + np.arange(n_grid)) % n_fft
        self.reference_energy = float(self.x2[kb != 0].sum() / nc)

    # Derived numbers (docs/delay_doppler.md section 5; same formulas as budget.delay_doppler)
    @property
    def total_bandwidth_hz(self) -> float:
        w = self.config.waveform
        ks = self.active_steps
        return (ks[-1] - ks[0]) * w.step_spacing_hz + w.chirp_bandwidth_hz

    @property
    def range_resolution_m(self) -> float:
        return SPEED_OF_LIGHT / (2 * self.total_bandwidth_hz)

    # Per-burst stages
    def burst_spectra(self, iq: np.ndarray) -> np.ndarray:
        """(..., num_samples) raw IQ -> (..., N_c) matched spectra Y (chirps averaged first)."""
        return matched_spectrum(average_chirps(iq, self.num_chirp_samples), self.replica)

    def calibrate(self, spectra: np.ndarray, steps: Iterable[int] | None = None) -> np.ndarray:
        """Y (..., num_steps, N_c) -> Z: leakage normalization, envelope alignment, tau_L (per step)."""
        cal = self.cal
        if cal is None or not cal.enabled:
            return np.asarray(spectra, dtype=complex)
        z = normalize_to_leakage(spectra, cal.tau_env_s, self.sample_rate_hz, cal.phase_only,
                                 self.reference_energy)
        if cal.tau_leak_s:
            ks = range(z.shape[-2]) if steps is None else steps
            for i, k in enumerate(ks):
                z[..., i, :] = apply_tau_leak(z[..., i, :], self.step_offsets_hz[k], cal.tau_leak_s,
                                              self.sample_rate_hz)
        return z

    def sweep_spectra(self, sweep: Sweep) -> SweepSpectra:
        """One Sweep (b210-12 reader) -> calibrated per-step spectra plus burst times."""
        ok = np.asarray(sweep.valid, bool) & np.asarray(sweep.present, bool)
        y = self.burst_spectra(sweep.iq)
        z = self.calibrate(y)
        z[~ok] = 0
        t = np.where(ok, np.asarray(sweep.usrp_ticks, float) / self.config.device.master_clock_rate_hz, np.nan)
        return SweepSpectra(int(sweep.sweep), z, t, ok)

    # Stitching and profiles
    def stitch(self, z: np.ndarray) -> np.ndarray:
        """Z (..., num_steps, N_c) -> windowed, equalized stitched spectrum (..., n_grid)."""
        z = np.asarray(z)
        out = np.zeros(z.shape[:-2] + (self.n_grid,), dtype=complex)
        for k, src, dst, wt in self._g:
            out[..., dst] = z[..., k, :][..., src] * wt
        for k, dst, w_lo, w_hi in self._dc:
            out[..., dst] = z[..., k, -1] * w_lo + z[..., k, 1] * w_hi
        return out

    def step_stitch(self, z: np.ndarray, k: int) -> np.ndarray:
        """Step k's share of the stitch: Z_k (..., N_c) -> (..., n_grid), only the bins step k keeps.

        Same keep/split mask, 1/|X|^2 equalisation, window and DC fill as `stitch`, so summing this over
        the steps of one sweep gives `stitch` exactly and no overlap bin is counted twice.
        """
        z = np.asarray(z)
        out = np.zeros(z.shape[:-1] + (self.n_grid,), dtype=complex)
        for kk, src, dst, wt in self._g:
            if kk == k:
                out[..., dst] = z[..., src] * wt
        for kk, dst, w_lo, w_hi in self._dc:
            if kk == k:
                out[..., dst] = z[..., -1] * w_lo + z[..., 1] * w_hi
        return out

    def step_profiles(self, z: np.ndarray, steps: np.ndarray) -> np.ndarray:
        """Per-burst complex profiles for backprojection: Z (num_bursts, N_c), step of each burst ->
        (num_bursts, len(ranges_m)), each burst's sub-band alone on the common grid (`step_stitch`).

        Phase convention: a point at two-way delay tau gives Z = A exp(-j 2 pi nu tau) at absolute RF
        nu = f_k + f (calibration.py module doc), so its profile is
        s(r) = A exp(-j 2 pi f_ref tau) sum_g W_g exp(+j 2 pi g delta_f (2 r / c - tau)), f_ref =
        `reference_freq_hz`; at r = c tau / 2 the phase is -2 pi f_ref tau.
        """
        z = np.asarray(z)
        steps = np.asarray(steps, dtype=int)
        out = np.zeros((z.shape[0], self.ranges_m.size), dtype=complex)
        for k in self.active_steps:
            rows = np.flatnonzero(steps == k)
            if rows.size:
                out[rows] = self.profile(self.step_stitch(z[rows], k))
        return out

    def profile(self, stitched: np.ndarray) -> np.ndarray:
        """Stitched spectrum (..., n_grid) -> complex range profile (..., len(ranges_m))."""
        s = np.asarray(stitched)
        full = np.zeros(s.shape[:-1] + (self.n_fft,), dtype=complex)
        full[..., self._place] = s
        return (np.fft.ifft(full, axis=-1) * self.n_fft)[..., self._ridx]


def _as_spectra(item: Sweep | SweepSpectra, processor: Processor) -> SweepSpectra:
    return item if isinstance(item, SweepSpectra) else processor.sweep_spectra(item)


def stitch_sweep(sweep: Sweep | SweepSpectra, processor: Processor) -> np.ndarray:
    """One sweep -> stitched, windowed spectrum on the 50 kHz grid (processor.grid_freqs_hz from f_c)."""
    return processor.stitch(_as_spectra(sweep, processor).spectra)


def range_profile(sweep: Sweep | SweepSpectra, processor: Processor) -> RangeProfile:
    """One sweep -> complex range profile (stitch-first; fine for static scenes)."""
    return RangeProfile(processor.ranges_m, processor.profile(stitch_sweep(sweep, processor)))


def fit_leakage_cal(
    sweeps: Iterable[Sweep],
    processor: Processor,
    tau_leak_s: float,
    search_s: tuple[float, float] | None = None,
    phase_only: bool = False,
) -> LeakageCal:
    """Fit tau_env once from static sweeps (calibration.estimate_tau_env) and return the LeakageCal."""
    ys = [processor.burst_spectra(s.iq)[np.asarray(s.valid, bool) & np.asarray(s.present, bool)] for s in sweeps]
    y = np.concatenate(ys)
    if y.size == 0:
        raise ValueError("no valid bursts to fit tau_env")
    tau_env = estimate_tau_env(y, processor.sample_rate_hz, search_s)
    return LeakageCal(tau_env, tau_leak_s, phase_only)


def mean_sweep_period(sweep_ids: np.ndarray, times_s: np.ndarray, valid: np.ndarray) -> float:
    """Mean sweep period from step-0 timestamps (first to last valid, divided by the sweep-index span)."""
    v = valid[:, 0]
    if v.sum() < 2:
        raise ValueError("need at least two sweeps with a valid step-0 burst to time the CPI")
    ids, t = sweep_ids[v], times_s[v, 0]
    return float((t[-1] - t[0]) / (ids[-1] - ids[0]))


def delay_doppler_map(
    sweeps: Sequence[Sweep | SweepSpectra],
    window: str | np.ndarray | None = "hann",
    *,
    processor: Processor,
    remove_static: bool = False,
) -> DelayDopplerMap:
    """M sweeps -> delay-Doppler map (Doppler per step at each burst's timestamp, then stitch).

    `window` is the slow-time (Doppler) window over the M sweeps: "hann" (default), "hamming",
    "rect" or an array. Missing/invalid bursts get zero weight. `remove_static` subtracts the
    per-step mean over the CPI first (display toggle; removes leakage and static clutter).
    """
    from b210_sar.processing.calibration import remove_static_clutter

    items = [_as_spectra(s, processor) for s in sweeps]
    m = len(items)
    if m < 2:
        raise ValueError("need at least two sweeps")
    z = np.stack([it.spectra for it in items])  # (M, N, Nc)
    t = np.stack([it.times_s for it in items])  # (M, N)
    v = np.stack([it.valid for it in items])  # (M, N)
    ids = np.array([it.sweep for it in items])
    if remove_static:
        z = remove_static_clutter(z, v)
    ts = mean_sweep_period(ids, t, v)
    t_mid = float(np.nanmean(np.where(v, t, np.nan)))
    w = _window(window, m)
    fd = (np.arange(m) - m // 2) / (m * ts)
    dt = np.where(v, t - t_mid, 0.0)
    d = np.empty((m,) + z.shape[1:], dtype=complex)
    for k in range(z.shape[1]):
        e = np.exp(-2j * np.pi * fd[:, None] * dt[None, :, k]) * (w * v[:, k])[None, :]
        wk = float((w * v[:, k]).sum())  # this step's valid-burst window sum (== w.sum() with no gaps)
        d[:, k, :] = (e @ z[:, k, :]) / (wk if wk > 0 else 1.0)
    mp = processor.profile(processor.stitch(d))
    return DelayDopplerMap(
        ranges_m=processor.ranges_m,
        doppler_hz=fd,
        velocity_mps=fd * processor.wavelength_m / 2,
        map=mp,
        sweep_period_s=ts,
        t_mid_s=t_mid,
        sweeps=ids,
        missing_bursts=int((~v).sum()),
        wavelength_m=processor.wavelength_m,
    )


def peak(ranges_m: np.ndarray, profile: np.ndarray, near_m: float | None = None, span_m: float = 5.0):
    """(range, |amplitude|) of the strongest sample (within span_m of `near_m`), refined by a parabola."""
    a = np.abs(profile)
    if near_m is not None:
        sel = np.flatnonzero(np.abs(ranges_m - near_m) <= span_m)
        i = int(sel[np.argmax(a[sel])])
    else:
        i = int(np.argmax(a))
    if 0 < i < a.size - 1:
        y0, y1, y2 = a[i - 1], a[i], a[i + 1]
        den = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / den if den != 0 else 0.0
        dr = ranges_m[i + 1] - ranges_m[i]
        return float(ranges_m[i] + off * dr), float(y1 - 0.25 * (y0 - y2) * off)
    return float(ranges_m[i]), float(a[i])
