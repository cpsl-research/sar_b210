"""T6: pipeline stages 2-3, chirp averaging and range compression (numpy only).

The matched filter is circular on one chirp period (docs/delay_doppler.md section 2): capture's TX
burst leads RX by one chirp, so every RX period is a full cyclic shift of the chirp and circular
compression is exact for tau + tau_sys <= T_c.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex64]


def average_chirps(burst: ComplexArray, chirp_samples: int) -> ComplexArray:
    """Coherently average the chirps of a dwell into one pulse.

    `burst` is (..., num_samples); a trailing partial period is dropped. Returns (..., chirp_samples).
    """
    b = np.asarray(burst)
    n = b.shape[-1] // chirp_samples
    if n < 1:
        raise ValueError(f"burst of {b.shape[-1]} samples is shorter than one chirp ({chirp_samples})")
    b = b[..., : n * chirp_samples].reshape(*b.shape[:-1], n, chirp_samples)
    return b.mean(axis=-2)


def matched_spectrum(pulse: ComplexArray, replica: ComplexArray) -> np.ndarray:
    """Y(f) = FFT(pulse) * conj(FFT(replica)), numpy FFT order, (..., N_c) complex128."""
    return np.fft.fft(pulse, axis=-1) * np.conj(np.fft.fft(replica))


def _zero_pad(spec: np.ndarray, upsample: int) -> np.ndarray:
    """Place an FFT-order spectrum in an `upsample`-times longer one (Nyquist bin split in half)."""
    n = spec.shape[-1]
    m = n * upsample
    out = np.zeros(spec.shape[:-1] + (m,), dtype=complex)
    h = (n + 1) // 2  # bins 0..h-1 are non-negative frequencies
    out[..., :h] = spec[..., :h]
    if n % 2 == 0:
        out[..., n // 2] = spec[..., n // 2] / 2
        out[..., m - n // 2] = spec[..., n // 2] / 2
        out[..., m - n // 2 + 1 :] = spec[..., n // 2 + 1 :]
    else:
        out[..., m - (n - h) :] = spec[..., h:]
    return out


def range_compress(
    pulse: ComplexArray, replica: ComplexArray, upsample: int = 4, window: str | None = None
) -> ComplexArray:
    """Circular matched filter against the replica, upsampled by zero-padding the spectrum.

    Output sample i is delay i / (upsample * f_s); a delay of d samples peaks at d * upsample.
    `window` "hann" tapers the matched spectrum over the replica's band (|X|^2 > -6 dB) for lower
    sidelobes; None (default) is the plain matched filter.
    """
    spec = matched_spectrum(pulse, replica)
    if window is not None:
        if window != "hann":
            raise ValueError(f"unknown window {window!r}")
        x2 = np.abs(np.fft.fft(replica)) ** 2
        keep = x2 > 0.25 * x2.max()
        idx = np.flatnonzero(np.fft.fftshift(keep))
        ws = np.zeros(x2.size)
        ws[idx[0] : idx[-1] + 1] = np.hanning(idx[-1] - idx[0] + 1)
        w = np.fft.ifftshift(ws) * keep
        spec = spec * w
    return np.fft.ifft(_zero_pad(spec, upsample), axis=-1) * upsample
