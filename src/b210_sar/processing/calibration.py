"""T6: pipeline stages 4-5, leakage phase calibration and static clutter removal."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex64]
FloatArray = npt.NDArray[np.float64]


def find_leakage_window(profiles: ComplexArray) -> slice:
    """Locate the leakage peak once from static data. The window is then held fixed."""
    raise NotImplementedError("T6")


def normalize_to_leakage(
    profile: ComplexArray, window: slice, phase_only: bool = False
) -> ComplexArray:
    """Divide a compressed pulse by the complex value of its leakage peak."""
    raise NotImplementedError("T6")


def estimate_tau_leak(reflector_phases: FloatArray, step_freqs_hz: FloatArray) -> float:
    """Fit tau_leak from the residual phase slope across steps on a surveyed corner reflector."""
    raise NotImplementedError("T6")


def tau_leak_from_separation(separation_m: float) -> float:
    """Compute tau_leak from the measured antenna separation."""
    raise NotImplementedError("T6")


def apply_tau_leak(profile: ComplexArray, step_freq_hz: float, tau_leak_s: float) -> ComplexArray:
    """Apply exp(+j 2 pi f_k tau_leak) to one step's pulse."""
    raise NotImplementedError("T6")


def remove_static_clutter(profiles: ComplexArray, steps: npt.NDArray[np.int_]) -> ComplexArray:
    """Subtract the mean calibrated profile per step across the run."""
    raise NotImplementedError("T6")
