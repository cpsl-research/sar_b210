"""T2: the tapered baseband chirp, the same formula as capture/src/chirp.cpp.

`generate_chirp` returns what capture writes as `chirp_replica.cf32`: the int16 transmit samples
divided by full scale, so it carries `tx_amplitude` and the int16 rounding. numpy only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from b210_sar.config import WaveformConfig

FULL_SCALE = 32767.0


def chirp_samples(waveform: WaveformConfig) -> int:
    """Samples per chirp period, as `chirp_samples` in capture/src/run_config.cpp."""
    return int(np.floor(waveform.chirp_length_s * waveform.sample_rate_hz + 0.5))


def dwell_chirps(waveform: WaveformConfig) -> int:
    """Chirps per dwell (per burst), as `dwell_chirps` in capture/src/run_config.cpp."""
    return int(np.floor(waveform.dwell_s / waveform.chirp_length_s + 0.5))


def _lround(x: np.ndarray) -> np.ndarray:
    """std::lround: halves away from zero (np.rint rounds halves to even)."""
    return np.sign(x) * np.floor(np.abs(x) + 0.5)


def generate_chirp_sc16(waveform: WaveformConfig) -> np.ndarray:
    """The transmitted chirp as int16 I/Q pairs, shape (N_c, 2), as capture's `generate_chirp`."""
    n_c = chirp_samples(waveform)
    fs = waveform.sample_rate_hz
    duration = n_c / fs
    bw = waveform.chirp_bandwidth_hz
    taper_len = int(np.floor(waveform.taper_fraction * n_c + 0.5))
    scale = waveform.tx_amplitude * FULL_SCALE
    n = np.arange(n_c)
    t = n / fs
    phase = 2 * np.pi * (-bw / 2 * t + bw / (2 * duration) * t * t)
    edge = np.minimum(n, n_c - 1 - n)
    taper = np.ones(n_c)
    if taper_len > 0:
        e = edge < taper_len
        taper[e] = 0.5 * (1 - np.cos(np.pi * (edge[e] + 0.5) / taper_len))
    out = np.empty((n_c, 2), dtype=np.int16)
    out[:, 0] = _lround(scale * taper * np.cos(phase))
    out[:, 1] = _lround(scale * taper * np.sin(phase))
    return out


def generate_chirp(waveform: WaveformConfig) -> np.ndarray:
    """One period of the linear FM chirp as complex64, with raised-cosine edge taper.

    Up-chirp -B/2 -> +B/2 at amplitude `tx_amplitude`, quantized to int16 and divided by 32767
    exactly as capture's `chirp_to_replica`, so it equals a run's `chirp_replica.cf32`.
    """
    s = generate_chirp_sc16(waveform).astype(np.float32)
    out = np.empty(s.shape[0], dtype=np.complex64)
    out.real = s[:, 0] / np.float32(FULL_SCALE)
    out.imag = s[:, 1] / np.float32(FULL_SCALE)
    return out


def write_replica(chirp: np.ndarray, path: str | Path) -> None:
    """Write the exact transmitted chirp as chirp_replica.cf32 (interleaved little-endian float32)."""
    np.asarray(chirp, dtype="<c8").tofile(Path(path))


def main(argv: list[str] | None = None) -> int:
    """CLI: sar-chirp CONFIG OUTPUT."""
    from b210_sar.config import load_config

    p = argparse.ArgumentParser(prog="sar-chirp", description="Write chirp_replica.cf32 for a config.")
    p.add_argument("config")
    p.add_argument("output")
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    write_replica(generate_chirp(cfg.waveform), a.output)
    print(f"{a.output}: {chirp_samples(cfg.waveform)} samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
