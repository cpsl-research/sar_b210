"""Pipeline stages 1-8 from a run folder (or console HDF5 recording) to images.

1 load and filter, 2 average chirps, 3 range compress, 4 leakage calibration,
5 clutter removal, 6 backprojection, 7 autofocus, 8 output.

Every burst is its own pulse at its own position (docs/design_notes.md): stages 2-4 run per burst
(`Processor.sweep_spectra`, tau_env fitted once on the first sweeps, tau_L = antenna.tx_rx_separation_m
/ c), stage 5 subtracts the per-step mean over the run (on by default when the antennas move more
than a wavelength), stage 6 is `backprojection.backproject` on each burst's own sub-band, stage 7
`autofocus.prominent_point_autofocus`. The image is divided by the number of sweeps, so a point
target reads a / a_L like the range profiles.

Output folder: image.npz (complex `image` [y, x], `x_m`, `y_m`, `z_m`, `subband_images` [step, y, x],
`autofocus_phase_rad` per used burst, `burst_index`, `options` JSON, `positions_sha256`, `tau_env_s`,
`tau_leak_s`, `reference_freq_hz`), image.png (dB, metre axes) and subband_<k>.png.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from b210_sar.dataset import POSITIONS_FILE, open_source
from b210_sar.positions import load_positions
from b210_sar.processing.autofocus import prominent_point_autofocus
from b210_sar.processing.backprojection import ImageGrid, backproject, bistatic_range
from b210_sar.processing.calibration import remove_static_clutter, tau_leak_from_separation
from b210_sar.processing.delay_doppler import Processor, fit_leakage_cal
from b210_sar.processing.png import write_db_png

SPEED_OF_LIGHT = 299792458.0


@dataclass(frozen=True)
class PipelineOptions:
    grid: ImageGrid | None = None  # None: default_grid(positions)
    tau_leak_s: float | None = None  # None: antenna.tx_rx_separation_m / c
    range_upsample: int = 16  # Processor pad; >= 16 keeps the linear interpolation loss < 0.1 dB
    autofocus: bool = True
    autofocus_iterations: int = 5
    remove_clutter: bool | None = None  # None: on for a moving run (antennas travel > one wavelength)
    per_subband_images: bool = True
    use_gpu: bool = False
    positions_path: str | Path | None = None  # None: RUN/antenna_positions.csv
    stream: str | None = None  # HDF5 SarBurst group, if the file has more than one
    cal_sweeps: int = 16  # sweeps used to fit tau_env
    dynamic_range_db: float = 40.0


@dataclass
class PipelineResult:
    image: np.ndarray
    grid: ImageGrid
    subband_images: np.ndarray | None
    autofocus_phase_rad: np.ndarray | None
    burst_index: np.ndarray
    peak_xy: tuple[float, float]
    output: Path


def default_grid(tx: np.ndarray, rx: np.ndarray, near_m: float = 2.0, far_m: float = 12.0,
                 dx: float = 0.02, dy: float = 0.1) -> ImageGrid:
    """Along the midpoint path's x extent, from near_m to far_m to the right of it (-y: antennas look right)."""
    mid = 0.5 * (np.asarray(tx) + np.asarray(rx))
    x0, x1 = float(mid[:, 0].min()), float(mid[:, 0].max())
    yc = float(np.median(mid[:, 1]))
    x = np.arange(np.floor(x0 / dx) * dx, x1 + dx / 2, dx)
    y = np.arange(np.round((yc - far_m) / dy) * dy, yc - near_m + dy / 2, dy)
    return ImageGrid(np.round(x, 9), np.round(y, 9), float(np.median(mid[:, 2])))


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process_run(run_path: str | Path, output_path: str | Path, options: PipelineOptions | None = None) -> PipelineResult:
    """Write image.npz, image.png and per-sub-band PNGs to `output_path` (a folder); see module doc."""
    opt = options or PipelineOptions()
    run_path, out = Path(run_path), Path(output_path)
    pos_path = Path(opt.positions_path) if opt.positions_path is not None else (
        run_path / POSITIONS_FILE if run_path.is_dir() else run_path.with_name(POSITIONS_FILE))
    with open_source(run_path, stream=opt.stream) as src:
        cfg = src.config
        if cfg is None:
            raise ValueError(f"{run_path}: no config; cannot process")
        replica = getattr(src, "replica", None)
        ids = src.sweep_ids
        if not ids:
            raise ValueError(f"{run_path}: no sweeps")
        n = cfg.waveform.num_steps
        tau_leak = opt.tau_leak_s if opt.tau_leak_s is not None else tau_leak_from_separation(
            cfg.antenna.tx_rx_separation_m)
        # stages 1-4: per-burst calibrated spectra (IQ read one sweep at a time)
        p0 = Processor(cfg, replica=replica)
        lc = fit_leakage_cal([src.sweep(i) for i in ids[: opt.cal_sweeps]], p0, tau_leak)
        pc = Processor(cfg, replica=replica, cal=lc)
        z = np.zeros((len(ids), n, pc.num_chirp_samples), dtype=complex)
        valid = np.zeros((len(ids), n), dtype=bool)
        times = np.full((len(ids), n), np.nan)
        bidx = np.full((len(ids), n), -1, dtype=np.int64)
        for m, i in enumerate(ids):
            s = src.sweep(i)
            sp = pc.sweep_spectra(s)
            z[m], valid[m], times[m], bidx[m] = sp.spectra, sp.valid, sp.times_s, s.burst_index
    steps = np.broadcast_to(np.arange(n), (len(ids), n))

    use = valid.reshape(-1)
    burst_index = bidx.reshape(-1)[use]
    pos = load_positions(pos_path, burst_index, cfg.antenna.tx_rx_separation_m)
    grid = opt.grid if opt.grid is not None else default_grid(pos.tx, pos.rx)

    # stage 5
    mid = 0.5 * (pos.tx + pos.rx)
    moving = float(np.max(np.linalg.norm(mid - mid[0], axis=1))) > SPEED_OF_LIGHT / cfg.rf.center_freq_hz
    clutter = moving if opt.remove_clutter is None else bool(opt.remove_clutter)
    if clutter:
        z = remove_static_clutter(z, valid)

    # stage 6: profiles on a range axis covering the grid
    corners = np.array([[x, y, grid.z_m] for x in (grid.x_m[0], grid.x_m[-1]) for y in (grid.y_m[0], grid.y_m[-1])])
    r_max = float(bistatic_range(pos, corners).max()) / 2 + 5.0
    p = Processor(cfg, replica=replica, cal=lc, pad=opt.range_upsample, min_range_m=-5.0, max_range_m=r_max)
    zu, su = z.reshape(-1, z.shape[-1])[use], steps.reshape(-1)[use]
    prof = p.step_profiles(zu, su)
    norm = max(use.sum() / n, 1.0)
    tu = times.reshape(-1)[use]
    img = backproject(prof, p.ranges_m, p.reference_freq_hz, pos, grid, use_gpu=opt.use_gpu) / norm

    # stage 7
    phase = None
    weights = np.ones(prof.shape[0], dtype=complex)
    if opt.autofocus:
        af = prominent_point_autofocus(prof, p.ranges_m, p.reference_freq_hz, pos, grid, times_s=tu,
                                       iterations=opt.autofocus_iterations, image=img * norm)
        img = af.image / norm
        phase = af.phase_rad
        weights = np.exp(-1j * phase)

    sub = None
    if opt.per_subband_images:
        sub = np.stack([backproject(prof, p.ranges_m, p.reference_freq_hz, pos, grid,
                                    weights=np.where(su == k, weights, 0)) / norm for k in range(n)])

    # stage 8
    out.mkdir(parents=True, exist_ok=True)
    o = {k: (str(v) if isinstance(v, Path) else v) for k, v in dataclasses.asdict(opt).items() if k != "grid"}
    o.update(remove_clutter_applied=clutter, grid_dx=float(np.diff(grid.x_m[:2])[0]) if grid.x_m.size > 1 else 0.0,
             grid_dy=float(np.diff(grid.y_m[:2])[0]) if grid.y_m.size > 1 else 0.0)
    np.savez_compressed(
        out / "image.npz", image=img, x_m=grid.x_m, y_m=grid.y_m, z_m=grid.z_m,
        subband_images=sub if sub is not None else np.zeros((0,) + grid.shape, complex),
        autofocus_phase_rad=phase if phase is not None else np.zeros(0), burst_index=burst_index,
        options=json.dumps(o), positions_file=str(pos_path), positions_sha256=_sha256(pos_path),
        run=str(run_path), tau_env_s=lc.tau_env_s, tau_leak_s=lc.tau_leak_s, reference_freq_hz=p.reference_freq_hz)
    write_db_png(out / "image.png", img, grid.x_m, grid.y_m, opt.dynamic_range_db)
    if sub is not None:
        for k in range(n):
            write_db_png(out / f"subband_{k}.png", sub[k], grid.x_m, grid.y_m, opt.dynamic_range_db)
    a = np.abs(img)
    iy, ix = np.unravel_index(int(np.argmax(a)), a.shape)
    return PipelineResult(img, grid, sub, phase, burst_index, (float(grid.x_m[ix]), float(grid.y_m[iy])), out)


def _axis(v: list[float] | None) -> np.ndarray | None:
    if v is None:
        return None
    lo, hi, step = v
    return np.round(np.arange(lo, hi + step / 2, step), 9)


def main(argv: list[str] | None = None) -> int:
    """CLI: sar-image RUN OUTPUT [--positions CSV] [--x MIN MAX STEP --y MIN MAX STEP] ..."""
    ap = argparse.ArgumentParser(prog="sar-image", description="Form a SAR image from a run (sim or recorded).")
    ap.add_argument("run", help="run folder or console HDF5 recording")
    ap.add_argument("output", help="output folder (image.npz, image.png, subband_<k>.png)")
    ap.add_argument("--positions", help="antenna_positions.csv (default: in the run folder)")
    ap.add_argument("--x", nargs=3, type=float, metavar=("MIN", "MAX", "STEP"), help="grid x axis, m")
    ap.add_argument("--y", nargs=3, type=float, metavar=("MIN", "MAX", "STEP"), help="grid y axis, m")
    ap.add_argument("--z", type=float, default=0.0, help="grid height, m (with --x/--y)")
    ap.add_argument("--no-autofocus", action="store_true")
    ap.add_argument("--clutter", choices=("auto", "on", "off"), default="auto", help="static clutter removal")
    ap.add_argument("--no-subbands", action="store_true", help="skip the per-sub-band debug images")
    ap.add_argument("--stream", help="HDF5 SarBurst group")
    ap.add_argument("--range-upsample", type=int, default=16)
    a = ap.parse_args(argv)
    if (a.x is None) != (a.y is None):
        ap.error("give both --x and --y, or neither")
    grid = ImageGrid(_axis(a.x), _axis(a.y), a.z) if a.x is not None else None
    opt = PipelineOptions(grid=grid, autofocus=not a.no_autofocus,
                          remove_clutter={"auto": None, "on": True, "off": False}[a.clutter],
                          per_subband_images=not a.no_subbands, positions_path=a.positions, stream=a.stream,
                          range_upsample=a.range_upsample)
    res = process_run(a.run, a.output, opt)
    print(f"{res.output}: image {res.image.shape[1]} x {res.image.shape[0]} px, peak at "
          f"x {res.peak_xy[0]:.3f} m, y {res.peak_xy[1]:.3f} m, |peak| {np.abs(res.image).max():.4g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
