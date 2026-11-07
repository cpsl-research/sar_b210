"""T9: synthetic stepped-chirp runs for testing processing before field data exists (numpy only).

Signal model: docs/delay_doppler.md section 2. Burst (sweep m, step k) at USRP time t_mk, RF step
f_k, random LO phase phi_mk (or 0 with `random_lo_phase=False`); every chirp period of the burst is

    r(t) = 32767 * exp(j phi) * sum_p a_p x(t - tau_sys - tau_p) exp(-j 2 pi f_k tau_p)  + dc + noise

with x the replica (`chirp.generate_chirp`, which carries tx_amplitude), paths p = the leakage
(tau_L = leakage path / c) and each target (tau = 2 (R - v (t - t_0)) / c, v closing speed). The
envelope is evaluated per burst (cyclic, exact fractional delay via the FFT); the carrier phase
per chirp, so Doppler inside the dwell is kept. IQ is rounded to int16 (clipped, with a warning).
Burst times: t_0 + m T_sweep + k T_step + N(0, jitter) like the measured 30.64 / 15.32 ms, or an
explicit (num_sweeps, num_steps) array, e.g. a real run's usrp_time_s.

Geometric scenes (b210-31): with per-burst antenna positions `tx_xyz`/`rx_xyz` (num_sweeps, num_steps, 3)
a target given by `xyz_m` has the bistatic delay tau = (|p_tx - x| + |p_rx - x|) / c at each burst
(stop-and-go inside the 1 ms dwell: 0.15 mm at 0.15 m/s), and the leakage path is |p_tx - p_rx| per
burst. `straight_pass` builds such a scene; `odometry_positions` makes the positions an odometry-only
estimate would report (scale error along-track, heading drift across). Range-only targets and scenes
are unchanged.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from b210_sar.chirp import FULL_SCALE, chirp_samples, dwell_chirps, generate_chirp
from b210_sar.config import RunConfig, step_centres
from b210_sar.dataset import BURST_DTYPE, Run, Sweep, write_run

SPEED_OF_LIGHT = 299792458.0


@dataclass(frozen=True)
class SimTarget:
    range_m: float | None = None  # half the two-way path at t_0 (first burst); None with xyz_m
    velocity_mps: float = 0.0  # closing speed, positive toward the radar (range-only targets)
    amplitude: complex = 0.1  # relative to the replica (leakage default 1.0)
    xyz_m: tuple[float, float, float] | None = None  # fixed point target; needs the scene's tx_xyz/rx_xyz

    def __post_init__(self) -> None:
        if (self.range_m is None) == (self.xyz_m is None):
            raise ValueError("SimTarget needs exactly one of range_m and xyz_m")
        if self.xyz_m is not None and self.velocity_mps:
            raise ValueError("an xyz_m target is fixed; its delay comes from the antenna positions")


@dataclass(frozen=True)
class SimScene:
    targets: tuple[SimTarget, ...] = ()
    num_sweeps: int = 64
    leakage_path_m: float | None = 3.0  # leakage path (antenna separation), tau_L = path / c; None: none
    leakage_amplitude: complex = 1.0
    tau_sys_samples: float = 159.3  # baseband latency, fractional on purpose
    noise_std: float = 0.0  # complex noise std per sample in sc16 counts (each of I, Q: /sqrt(2))
    dc_offset: float = 0.0  # DC magnitude in counts, random phase per burst
    random_lo_phase: bool = True
    step_period_s: float = 15.32e-3  # measured run_20261106_155516_rx
    sweep_period_s: float | None = None  # None: num_steps * step_period_s
    timing_jitter_s: float = 0.34e-3
    burst_times_s: np.ndarray | None = field(default=None, compare=False)  # (num_sweeps, num_steps)
    start_time_s: float = 0.2
    seed: int = 0
    tx_xyz: np.ndarray | None = field(default=None, compare=False)  # (num_sweeps, num_steps, 3) metres
    rx_xyz: np.ndarray | None = field(default=None, compare=False)  # (num_sweeps, num_steps, 3) metres

    @property
    def tau_leak_s(self) -> float:
        return 0.0 if self.leakage_path_m is None else self.leakage_path_m / SPEED_OF_LIGHT

    @property
    def geometric(self) -> bool:
        return self.tx_xyz is not None or self.rx_xyz is not None


def _positions(scene: SimScene, n: int) -> tuple[np.ndarray, np.ndarray]:
    if scene.tx_xyz is None or scene.rx_xyz is None:
        raise ValueError("a geometric scene needs both tx_xyz and rx_xyz")
    tx, rx = np.asarray(scene.tx_xyz, float), np.asarray(scene.rx_xyz, float)
    for name, a in (("tx_xyz", tx), ("rx_xyz", rx)):
        if a.shape != (scene.num_sweeps, n, 3):
            raise ValueError(f"{name} has shape {a.shape}, need ({scene.num_sweeps}, {n}, 3)")
    return tx, rx


def straight_pass(
    config: RunConfig,
    start_xyz: tuple[float, float, float],
    velocity_xyz: tuple[float, float, float],
    tx_offset_xyz: tuple[float, float, float],
    rx_offset_xyz: tuple[float, float, float],
    **scene_fields,
) -> SimScene:
    """A SimScene on a straight line: reference point start + v (t - t_0), antennas at fixed offsets.

    `scene_fields` are any other SimScene fields (targets, num_sweeps, seed, ...). The burst times are
    drawn once (`burst_times`) and stored as `burst_times_s`, so they match the positions; the leakage
    path defaults to |tx_offset - rx_offset|. Example (first experiment, memo section B): travel +x at
    0.15 m/s, antennas looking right (-y), TX 0.075 m ahead and RX 0.075 m behind the reference point.
    """
    scene_fields.setdefault("leakage_path_m", float(np.linalg.norm(np.subtract(tx_offset_xyz, rx_offset_xyz))))
    scene = SimScene(**scene_fields)
    t = burst_times(config, scene)
    p = np.asarray(start_xyz, float) + (t - t[0, 0])[..., None] * np.asarray(velocity_xyz, float)
    return dataclasses.replace(scene, burst_times_s=t, tx_xyz=p + np.asarray(tx_offset_xyz, float),
                               rx_xyz=p + np.asarray(rx_offset_xyz, float))


def odometry_positions(
    tx_xyz: np.ndarray,
    rx_xyz: np.ndarray,
    scale_error: float = 0.0,
    heading_drift_rad: float = 0.0,
    lateral_m: tuple[float, float] = (0.0, 0.0),
) -> tuple[np.ndarray, np.ndarray]:
    """Positions a wheel-odometry-only estimate would report for a straight pass along x.

    Error model (the true path is the reference): the along-track distance travelled is scaled by
    (1 + scale_error); the heading drifts linearly from 0 to `heading_drift_rad` over the pass, so the
    cross-track error is y_err(s) = heading_drift_rad * s^2 / (2 L) at distance s of L; plus an optional
    sinusoidal cross-track error `lateral_m` = (amplitude, period in metres of travel). Both antennas
    get the same displacement, so |tx - rx| is unchanged (no attitude modelling, owner rule).
    """
    tx, rx = np.asarray(tx_xyz, float), np.asarray(rx_xyz, float)
    mid = 0.5 * (tx + rx)
    x0 = mid.reshape(-1, 3)[0, 0]
    s = mid[..., 0] - x0
    length = float(np.abs(s).max()) or 1.0
    d = np.zeros_like(mid)
    d[..., 0] = scale_error * s
    d[..., 1] = heading_drift_rad * s ** 2 / (2 * length)
    amp, period = lateral_m
    if amp:
        d[..., 1] += amp * np.sin(2 * np.pi * s / period)
    return tx + d, rx + d


def burst_times(config: RunConfig, scene: SimScene) -> np.ndarray:
    """(num_sweeps, num_steps) burst start times in seconds, quantized to master-clock ticks."""
    n = config.waveform.num_steps
    if scene.burst_times_s is not None:
        t = np.asarray(scene.burst_times_s, dtype=float)
        if t.shape != (scene.num_sweeps, n):
            raise ValueError(f"burst_times_s has shape {t.shape}, need ({scene.num_sweeps}, {n})")
    else:
        rng = np.random.default_rng([scene.seed, 1])
        ts = scene.sweep_period_s if scene.sweep_period_s is not None else n * scene.step_period_s
        t = (scene.start_time_s + np.arange(scene.num_sweeps)[:, None] * ts
             + np.arange(n)[None, :] * scene.step_period_s)
        if scene.timing_jitter_s > 0:
            t = t + rng.normal(0.0, scene.timing_jitter_s, t.shape)
    mclk = config.device.master_clock_rate_hz
    return np.round(t * mclk) / mclk


def simulate_bursts(config: RunConfig, scene: SimScene) -> tuple[np.ndarray, np.ndarray]:
    """Complex IQ in counts, (num_sweeps, num_steps, num_samples) complex128, and the burst times."""
    w = config.waveform
    nc, nch, n, fs = chirp_samples(w), dwell_chirps(w), w.num_steps, w.sample_rate_hz
    X = np.fft.fft(generate_chirp(w).astype(np.complex128)) * FULL_SCALE
    f = np.fft.fftfreq(nc, 1.0 / fs)
    fk = np.array(step_centres(config))
    t = burst_times(config, scene)
    t0 = t[0, 0]
    rng_phase = np.random.default_rng([scene.seed, 2])
    rng_noise = np.random.default_rng([scene.seed, 3])
    rng_dc = np.random.default_rng([scene.seed, 4])
    phi = rng_phase.uniform(-np.pi, np.pi, t.shape) if scene.random_lo_phase else np.zeros(t.shape)
    tau_sys = scene.tau_sys_samples / fs
    chirp_t = np.arange(nch) * nc / fs  # chirp start offsets inside the burst
    shape = (scene.num_sweeps, n)
    paths = []  # (amplitude, delay per burst (num_sweeps, num_steps), closing speed)
    if scene.geometric:
        tx, rx = _positions(scene, n)
        if scene.leakage_path_m is not None:
            paths.append((complex(scene.leakage_amplitude), np.linalg.norm(tx - rx, axis=-1) / SPEED_OF_LIGHT, 0.0))
    elif scene.leakage_path_m is not None:
        paths.append((complex(scene.leakage_amplitude), np.full(shape, scene.tau_leak_s), 0.0))
    for tg in scene.targets:
        if tg.xyz_m is not None:
            if not scene.geometric:
                raise ValueError("an xyz_m target needs the scene's tx_xyz/rx_xyz")
            x = np.asarray(tg.xyz_m, float)
            tau = (np.linalg.norm(tx - x, axis=-1) + np.linalg.norm(rx - x, axis=-1)) / SPEED_OF_LIGHT
            paths.append((complex(tg.amplitude), tau, 0.0))
        else:
            v = float(tg.velocity_mps)
            paths.append((complex(tg.amplitude), 2 * tg.range_m / SPEED_OF_LIGHT - 2 * v * (t - t0) / SPEED_OF_LIGHT, v))
    out = np.zeros((scene.num_sweeps, n, nch * nc), dtype=complex)
    for m in range(scene.num_sweeps):
        for k in range(n):
            burst = np.zeros((nch, nc), dtype=complex)
            for a, taus, v in paths:
                tau = taus[m, k]
                env = np.fft.ifft(X * np.exp(-2j * np.pi * f * (tau_sys + tau)))
                carrier = a * np.exp(-2j * np.pi * ((fk[k] * tau) % 1.0))
                if v:
                    # carrier phase advance inside the dwell: tau(t + dt) = tau - 2 v dt / c
                    carrier = carrier * np.exp(2j * np.pi * fk[k] * 2 * v * chirp_t / SPEED_OF_LIGHT)
                    burst += carrier[:, None] * env[None, :]
                else:
                    burst += carrier * env[None, :]
            b = burst.reshape(-1) * np.exp(1j * phi[m, k])
            if scene.dc_offset:
                b = b + scene.dc_offset * np.exp(1j * rng_dc.uniform(-np.pi, np.pi))
            if scene.noise_std:
                s = scene.noise_std / np.sqrt(2)
                b = b + rng_noise.normal(0, s, b.size) + 1j * rng_noise.normal(0, s, b.size)
            out[m, k] = b
    return out, t


def simulate_run(config: RunConfig, scene: SimScene, path: str | Path | None = None) -> Run:
    """Simulate a complete run in the capture layout; with `path`, also write it there via write_run."""
    iq, t = simulate_bursts(config, scene)
    m, n, ns = iq.shape
    flat = np.empty((m * n * ns, 2))
    flat[:, 0] = iq.real.reshape(-1)
    flat[:, 1] = iq.imag.reshape(-1)
    flat = np.rint(flat)
    clipped = int(np.count_nonzero(np.abs(flat) > 32767))
    if clipped:
        warnings.warn(f"sim: {clipped} I/Q values clipped to int16", stacklevel=2)
    flat = np.clip(flat, -32768, 32767).astype(np.int16).reshape(-1)
    fk = np.array(step_centres(config))
    mclk = config.device.master_clock_rate_hz
    bursts = np.zeros(m * n, dtype=BURST_DTYPE)
    i = np.arange(m * n)
    bursts["index"] = i
    bursts["sweep"] = i // n
    bursts["step"] = i % n
    bursts["sample_offset"] = i * ns
    bursts["num_samples"] = ns
    ticks = np.round(t.reshape(-1) * mclk).astype(np.int64)
    bursts["usrp_ticks"] = ticks
    bursts["usrp_time_s"] = ticks / mclk
    bursts["tx_freq_hz"] = fk[i % n]
    bursts["rx_freq_hz"] = fk[i % n]
    bursts["lo_offset_hz"] = config.rf.lo_offset_hz
    bursts["tx_gain_db"] = config.rf.tx_gain_db
    bursts["rx_gain_db"] = config.rf.rx_gain_db
    bursts["valid"] = True
    run = Run(path=Path(path) if path is not None else None, config=config, bursts=bursts, iq=flat,
              replica=generate_chirp(config.waveform))
    if path is not None:
        write_run(path, run)
    return run


def run_sweeps(run: Run) -> list[Sweep]:
    """In-memory Sweeps of a complete simulated Run (the same shape `dataset.open_source` yields)."""
    n = run.config.waveform.num_steps
    ns = int(run.bursts["num_samples"][0])
    iq = run.iq.astype(np.float32).reshape(-1, n, ns, 2)
    out = []
    for m in range(iq.shape[0]):
        rows = run.bursts[m * n : (m + 1) * n]
        c = np.empty((n, ns), dtype=np.complex64)
        c.real, c.imag = iq[m, ..., 0], iq[m, ..., 1]
        out.append(Sweep(sweep=int(rows["sweep"][0]), iq=c, tx_freq_hz=rows["tx_freq_hz"].astype(float),
                         rx_freq_hz=rows["rx_freq_hz"].astype(float), valid=rows["valid"].astype(bool),
                         usrp_ticks=rows["usrp_ticks"].astype(np.int64), present=np.ones(n, bool),
                         burst_index=rows["index"].astype(np.int64)))
    return out


def scene_from_mapping(raw: dict) -> SimScene:
    """SimScene from a YAML mapping: scene fields, plus `targets: [{range_m, velocity_mps, amplitude}]`."""
    raw = dict(raw or {})
    targets = tuple(SimTarget(**t) for t in raw.pop("targets", []) or [])
    return SimScene(targets=targets, **raw)


def main(argv: list[str] | None = None) -> int:
    """CLI: sar-sim CONFIG SCENE OUTPUT."""
    from b210_sar.config import load_config

    p = argparse.ArgumentParser(prog="sar-sim", description="Write a simulated run folder.")
    p.add_argument("config")
    p.add_argument("scene", help="YAML: SimScene fields and a targets list")
    p.add_argument("output")
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    scene = scene_from_mapping(yaml.safe_load(Path(a.scene).read_text()))
    run = simulate_run(cfg, scene, a.output)
    print(f"{a.output}: {run.bursts.size} bursts, {scene.num_sweeps} sweeps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
