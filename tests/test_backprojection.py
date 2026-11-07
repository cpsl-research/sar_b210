"""b210-31 Step 2: per-burst bistatic backprojection on the first-experiment geometry (simulated).

Scene: 2 x 40 MHz / 38 MHz config, straight pass along +x at 0.15 m/s, antennas looking right (-y), TX
0.075 m ahead and RX 0.075 m behind the reference point (d = 0.15 m), point target at (0, -6, 0).
Dwell shortened to 2 chirps (noise-free, so identical spectra) to keep memory small.

"Ideal" input is Z = Y exp(+j 2 pi f tau_sys) / E on a scene without leakage or LO phase: exactly the
calibrated model Z_k = A exp(-j 2 pi nu tau) (calibration.py), with no leakage-calibration error. The
calibrated-chain test runs the real path: leakage, random LO phases, fitted tau_env, tau_L, clutter removal.

Range cut vs the stitched profile: at a 2.5 m aperture and 6 m range the aperture spans +-11.8 deg, so the
image spectrum is an annular sector whose cross-track extent at the centre (2 k (1 - cos)) adds ~2.2 rad/m
to the 3.27 rad/m of the 78 MHz band: the range cut is narrower (1.9 m vs 2.84 m) and its sidelobes differ
from the 1D profile's (range-azimuth coupling, not the backprojector). So the literal comparison with the
stitched profile is made on a 0.3 m aperture (coupling negligible), and the 2.5 m image is compared with a
stitch-first image (whole stitched sweep at the sweep's mean position) of the same data.
"""

from __future__ import annotations

import dataclasses
from functools import lru_cache

import numpy as np
import pytest

from b210_sar import sim
from b210_sar.config import config_dir, load_config, step_centres
from b210_sar.positions import AntennaPositions
from b210_sar.processing import calibration as cal
from b210_sar.processing import delay_doppler as dd
from b210_sar.processing.backprojection import ImageGrid, backproject

C = 299792458.0
CFG = load_config(config_dir() / "sar_first_2x40.yaml")
SMALL = dataclasses.replace(CFG, waveform=dataclasses.replace(CFG.waveform, dwell_s=4e-5))
FS = CFG.waveform.sample_rate_hz
D = CFG.antenna.tx_rx_separation_m
LAM = C / CFG.rf.center_freq_hz
V = 0.15
TARGET = (0.0, -6.0, 0.0)
DX, DY = 0.01, 0.05


def lobe(axis, cut, span):
    """(-3 dB width, PSL dB within span of the peak outside its mainlobe, peak index)."""
    a = np.abs(cut)
    i = int(np.argmax(a))
    db = 20 * np.log10(a / a[i] + 1e-15)
    j = i
    while db[j] > -3:
        j -= 1
    left = axis[j] + (-3 - db[j]) / (db[j + 1] - db[j]) * (axis[j + 1] - axis[j])
    j = i
    while db[j] > -3:
        j += 1
    right = axis[j - 1] + (-3 - db[j - 1]) / (db[j] - db[j - 1]) * (axis[j] - axis[j - 1])
    lo = i
    while lo > 0 and a[lo - 1] < a[lo]:
        lo -= 1
    hi = i
    while hi < a.size - 1 and a[hi + 1] < a[hi]:
        hi += 1
    k = np.arange(a.size)
    side = (np.abs(axis - axis[i]) < span) & ((k < lo) | (k > hi))
    return right - left, (float(db[side].max()) if side.any() else -np.inf), i


def scene(length_m, **kw):
    nsw = int(round(length_m / V / (2 * 15.32e-3)))
    return sim.straight_pass(SMALL, (-length_m / 2, 0, 0), (V, 0, 0), (D / 2, 0, 0), (-D / 2, 0, 0),
                             num_sweeps=nsw, seed=1, targets=(sim.SimTarget(xyz_m=TARGET, amplitude=0.1),), **kw)


def processor(**kw):
    return dd.Processor(SMALL, pad=16, min_range_m=-5.0, max_range_m=30.0, **kw)


@lru_cache(maxsize=None)
def ideal(length_m):
    """(processor, Z (bursts, N_c), steps, positions, scene) for an ideal-calibration scene."""
    sc = scene(length_m, leakage_path_m=None, random_lo_phase=False)
    p = processor()
    y = np.concatenate([p.burst_spectra(s.iq) for s in sim.run_sweeps(sim.simulate_run(SMALL, sc))])
    z = y * np.exp(2j * np.pi * cal.fft_freqs(y.shape[-1], FS) * sc.tau_sys_samples / FS) / p.reference_energy
    steps = np.tile(np.arange(SMALL.waveform.num_steps), sc.num_sweeps)
    pos = AntennaPositions(sc.tx_xyz.reshape(-1, 3), sc.rx_xyz.reshape(-1, 3))
    return p, z, steps, pos, sc


def grid(half_x):
    return ImageGrid(np.round(np.arange(-half_x, half_x + 1e-9, DX), 6), np.round(np.arange(-12.0, -0.5 + 1e-9, DY), 6))


def peak_cuts(img, g):
    a = np.abs(img)
    iy, ix = np.unravel_index(int(np.argmax(a)), a.shape)
    return iy, ix, a[iy, ix]


def stitched_reference():
    """1D stitched profile of the same target, parked at broadside: (-3 dB width, PSL)."""
    p, z, _, _, sc = ideal(0.3)
    m = sc.num_sweeps // 2
    prof = p.profile(p.stitch(z[2 * m:2 * m + 2]))
    r = p.ranges_m
    sel = (r > 0) & (r < 20)
    return lobe(r[sel], prof[sel], 5.0)[:2]


def test_per_step_profiles_sum_to_stitched_profile():
    p, z, steps, _, _ = ideal(0.3)
    prof = p.step_profiles(z[:2], steps[:2])
    ref = p.profile(p.stitch(z[:2]))
    assert np.max(np.abs(prof.sum(axis=0) - ref)) < 1e-9 * np.max(np.abs(ref))
    assert p.reference_freq_hz == pytest.approx(CFG.rf.center_freq_hz)
    # each grid bin comes from exactly one step
    owned = np.zeros(p.n_grid, int)
    for _, _, dst, _ in p._g:
        owned[dst] += 1
    assert owned.max() == 1


@pytest.mark.parametrize("length_m", [2.5])
def test_point_target_focus_2p5m_pass(length_m):
    p, z, steps, pos, sc = ideal(length_m)
    g = grid(0.6)
    prof = p.step_profiles(z, steps)
    img = backproject(prof, p.ranges_m, p.reference_freq_hz, pos, g)
    iy, ix, pk = peak_cuts(img, g)
    # peak within one pixel of the truth
    assert abs(g.x_m[ix] - TARGET[0]) <= DX + 1e-9 and abs(g.y_m[iy] - TARGET[1]) <= DY + 1e-9
    # along-track -3 dB width vs lambda r / (2 L), L = the midpoint's travel
    mid = 0.5 * (sc.tx_xyz + sc.rx_xyz)
    L = mid[..., 0].max() - mid[..., 0].min()
    wx, pslx, _ = lobe(g.x_m, img[iy], 0.5)
    theory = LAM * abs(TARGET[1]) / (2 * L)
    assert abs(wx / theory - 1) < 0.25, (wx, theory)
    # range cut and PSL vs a stitch-first image of the same data (no double-counted overlap)
    m = sc.num_sweeps
    zs = z.reshape(m, SMALL.waveform.num_steps, -1)
    pos_s = AntennaPositions(sc.tx_xyz.mean(axis=1), sc.rx_xyz.mean(axis=1))
    img_s = backproject(p.profile(p.stitch(zs)), p.ranges_m, p.reference_freq_hz, pos_s, g)
    wy, psly, _ = lobe(g.y_m, img[:, ix], 5.0)
    wy_s, psly_s, _ = lobe(g.y_m, img_s[:, ix], 5.0)
    assert abs(wy / wy_s - 1) < 0.15 and abs(psly - psly_s) < 2.0, (wy, wy_s, psly, psly_s)
    assert np.abs(img_s).max() == pytest.approx(pk, rel=0.01)
    # coherent gain: every burst adds in phase at the peak (|sum| / sum|.| ~ 1)
    from b210_sar.processing.backprojection import contributions

    c = contributions(prof, p.ranges_m, p.reference_freq_hz, pos, np.array([[g.x_m[ix], g.y_m[iy], 0.0]]))[:, 0]
    assert abs(c.sum()) / np.abs(c).sum() > 0.99

    # negative controls: the wrong sign, or each step's own centre f_k as reference, defocus the image
    wrong = backproject(prof, p.ranges_m, -p.reference_freq_hz, pos, g)
    assert np.abs(wrong).max() < 0.3 * pk
    fk = step_centres(SMALL)
    own = sum(backproject(prof[steps == k], p.ranges_m, fk[k], AntennaPositions(pos.tx[steps == k], pos.rx[steps == k]), g)
              for k in range(SMALL.waveform.num_steps))
    # f_k leaves the steps 2 pi Delta_f R / c apart (3.3 rad mod 2 pi at R = 12 m): measured -2.0 dB; wrong sign -14.3 dB
    assert np.abs(own).max() < 0.85 * pk


def test_short_aperture_range_cut_matches_stitched_profile():
    """0.3 m pass (range-azimuth coupling negligible): the image's range cut is the stitched profile."""
    p, z, steps, pos, _ = ideal(0.3)
    g = ImageGrid(np.array([-0.02, 0.0, 0.02]), grid(0.1).y_m)
    img = backproject(p.step_profiles(z, steps), p.ranges_m, p.reference_freq_hz, pos, g)
    iy, ix, _ = peak_cuts(img, g)
    assert abs(g.y_m[iy] - TARGET[1]) <= DY + 1e-9
    wy, psly, _ = lobe(g.y_m, img[:, ix], 5.0)
    w_ref, psl_ref = stitched_reference()
    assert abs(wy / w_ref - 1) < 0.15 and abs(psly - psl_ref) < 2.0, (wy, w_ref, psly, psl_ref)


def _double_counted_profiles(p, z, steps):
    """Negative control: each step keeps its whole strong band (no midpoint split), common window."""
    nc = p.num_chirp_samples
    kb = np.rint(np.fft.fftfreq(nc) * nc).astype(int)
    d = int(round(SMALL.waveform.step_spacing_hz / p.bin_hz))
    g0 = -((p.num_steps - 1) * d // 2)
    strong = p.x2 > 10 ** (p.keep_db / 10) * p.x2.max()
    strong[0] = False
    win = np.hanning(p.n_grid)
    out = np.zeros((z.shape[0], p.ranges_m.size), complex)
    for k in range(p.num_steps):
        src = np.flatnonzero(strong)
        dst = kb[src] + k * d + g0 - p.grid_min
        ok = (dst >= 0) & (dst < p.n_grid)
        src, dst = src[ok], dst[ok]
        s = np.zeros((int((steps == k).sum()), p.n_grid), complex)
        s[:, dst] = z[steps == k][:, src] * win[dst] / p.x2[src]
        out[steps == k] = p.profile(s) / win.sum()
    return out


def test_double_counted_overlap_size():
    """Measures the memo's HYPOTHESIS (summing whole sub-bands, 2 MHz overlap counted twice, is a small
    effect). Printed, not asserted: the masked version is what `step_profiles` does, and
    test_per_step_profiles_sum_to_stitched_profile proves each bin is used once."""
    p, z, steps, pos, _ = ideal(0.3)
    g = ImageGrid(np.array([0.0]), grid(0.1).y_m)
    good = backproject(p.step_profiles(z, steps), p.ranges_m, p.reference_freq_hz, pos, g)[:, 0]
    dbl = backproject(_double_counted_profiles(p, z, steps), p.ranges_m, p.reference_freq_hz, pos, g)[:, 0]
    w_good, psl_good, _ = lobe(g.y_m, good, 5.0)
    w_dbl, psl_dbl, _ = lobe(g.y_m, dbl, 5.0)
    print(f"masked: width {w_good:.3f} m PSL {psl_good:.2f} dB; double-counted: width {w_dbl:.3f} m PSL {psl_dbl:.2f} dB")
    assert np.isfinite(psl_dbl)


def test_calibrated_chain_focuses():
    """Real path: leakage (d = 0.15 m), random LO phase per burst, tau_env fitted, tau_L applied, clutter removed."""
    sc = scene(2.5)
    sweeps = sim.run_sweeps(sim.simulate_run(SMALL, sc))
    p0 = processor()
    lc = dd.fit_leakage_cal(sweeps[:8], p0, cal.tau_leak_from_separation(D))
    assert lc.tau_env_s == pytest.approx(sc.tau_sys_samples / FS + D / C, abs=0.01 / FS)
    p = processor(cal=lc)
    z = np.stack([p.sweep_spectra(s).spectra for s in sweeps])
    z = cal.remove_static_clutter(z, np.ones(z.shape[:2], bool)).reshape(-1, z.shape[-1])
    steps = np.tile(np.arange(SMALL.waveform.num_steps), sc.num_sweeps)
    pos = AntennaPositions(sc.tx_xyz.reshape(-1, 3), sc.rx_xyz.reshape(-1, 3))
    g = grid(0.3)
    img = backproject(p.step_profiles(z, steps), p.ranges_m, p.reference_freq_hz, pos, g) / sc.num_sweeps
    iy, ix, pk = peak_cuts(img, g)
    assert abs(g.x_m[ix]) <= DX + 1e-9 and abs(g.y_m[iy] - TARGET[1]) <= DY + 1e-9
    # amplitude in leakage units: a / a_L = 0.1, less the clutter-removal loss (mean over the pass)
    assert 0.08 < pk < 0.105, pk
    mid = 0.5 * (sc.tx_xyz + sc.rx_xyz)
    wx, _, _ = lobe(g.x_m, img[iy], 0.25)
    assert abs(wx / (LAM * 6 / (2 * (mid[..., 0].max() - mid[..., 0].min()))) - 1) < 0.25
