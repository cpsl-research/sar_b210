"""b210-13 Step 3: chirp, simulator, pulse compression, calibration, stitching and delay-Doppler.

Test numbers 1-12 follow docs/delay_doppler.md section 8. Noise-free unless stated. The sim uses
random per-burst LO phases, tau_sys = 159.3 samples and a 3 m leakage path (tau_L = 3 m / c).
"""

from __future__ import annotations

import os
import dataclasses
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from b210_sar import budget, sim
from b210_sar.chirp import generate_chirp, write_replica
from b210_sar.config import config_dir, load_config
from b210_sar.dataset import open_source
from b210_sar.processing import calibration as cal
from b210_sar.processing import delay_doppler as dd
from b210_sar.processing.pulse import average_chirps, range_compress

C = 299792458.0
pytestmark = pytest.mark.filterwarnings("error::UserWarning")  # e.g. sim clipping

RUN = Path(os.environ.get("B210_TEST_RUN", "/home/rover/Documents/console/data/b210_runs/run_20261106_155516_rx"))
CFG = load_config(config_dir() / "default.yaml")
FS = CFG.waveform.sample_rate_hz
LAM = C / CFG.rf.center_freq_hz


def cfg_with(**waveform):
    return dataclasses.replace(CFG, waveform=dataclasses.replace(CFG.waveform, **waveform))


def sweeps_of(scene, cfg=CFG):
    return sim.run_sweeps(sim.simulate_run(cfg, scene))


def true_cal(scene, tau_leak_sign=1.0):
    return cal.LeakageCal(scene.tau_sys_samples / FS + scene.tau_leak_s, tau_leak_sign * scene.tau_leak_s)


def lobe_metrics(r, p, r0, span_m=12.0):
    """(-3 dB width m, PSL dB within span_m of the peak outside its mainlobe, peak |p|, peak range)."""
    a = np.abs(p)
    i = int(np.argmin(np.abs(r - r0)))
    i = i - 40 + int(np.argmax(a[i - 40 : i + 40]))
    db = 20 * np.log10(a / a[i] + 1e-15)
    j = i
    while db[j] > -3:
        j -= 1
    left = r[j] + (-3 - db[j]) / (db[j + 1] - db[j]) * (r[j + 1] - r[j])
    j = i
    while db[j] > -3:
        j += 1
    right = r[j - 1] + (-3 - db[j - 1]) / (db[j] - db[j - 1]) * (r[j] - r[j - 1])
    lo = i
    while a[lo - 1] < a[lo]:
        lo -= 1
    hi = i
    while a[hi + 1] < a[hi]:
        hi += 1
    x = np.arange(a.size)
    side = (np.abs(r - r[i]) < span_m) & ((x < lo) | (x > hi))
    return right - left, float(db[side].max()), float(a[i]), float(r[i])


def dip_db(r, p, r1, r2):
    """Dip between two targets: smaller of the two highest local maxima over the minimum between (dB)."""
    a = np.abs(p)
    sel = np.flatnonzero((r >= r1 - 1.0) & (r <= r2 + 1.0))
    seg = a[sel]
    peaks = [k for k in range(1, seg.size - 1) if seg[k] >= seg[k - 1] and seg[k] > seg[k + 1]]
    if len(peaks) < 2:
        return 0.0
    top = sorted(sorted(peaks, key=lambda k: seg[k])[-2:])
    lo = seg[top[0] : top[1] + 1].min()
    return float(20 * np.log10(min(seg[top[0]], seg[top[1]]) / lo))


# --- 1. chirp ------------------------------------------------------------------------------------


@pytest.mark.skipif(not (RUN / "chirp_replica.cf32").exists(), reason="real run folder not on this machine")
def test_01_generate_chirp_matches_real_replica(tmp_path):
    real = np.fromfile(RUN / "chirp_replica.cf32", dtype=np.complex64)
    g = generate_chirp(load_config(RUN / "config.yaml").waveform)
    assert g.dtype == np.complex64 and g.shape == real.shape == (1120,)
    assert np.abs(g - real).max() <= 3e-5
    write_replica(g, tmp_path / "r.cf32")
    assert np.array_equal(np.fromfile(tmp_path / "r.cf32", dtype=np.complex64), g)


def test_01b_chirp_shape_and_band():
    g = generate_chirp(CFG.waveform).astype(complex)
    assert np.abs(g).max() == pytest.approx(0.7, abs=1e-4)
    x2 = np.abs(np.fft.fft(g)) ** 2
    f = np.fft.fftfreq(g.size, 1 / FS)
    occupied = f[x2 > 0.25 * x2.max()]
    assert occupied.min() == pytest.approx(-22.5e6, abs=1.5e6)
    assert occupied.max() == pytest.approx(22.5e6, abs=1.5e6)


# --- 2-3. pulse ----------------------------------------------------------------------------------


def test_02_average_chirps_returns_pulse_and_17db_gain():
    rng = np.random.default_rng(2)
    x = generate_chirp(CFG.waveform).astype(np.complex64)
    burst = np.tile(x, 50)
    assert np.allclose(average_chirps(burst, 1120), x, atol=1e-6)
    noise = (rng.normal(size=(200, 56000)) + 1j * rng.normal(size=(200, 56000))).astype(np.complex64)
    gain = 10 * np.log10(noise.var() / average_chirps(noise, 1120).var())
    assert gain == pytest.approx(10 * np.log10(50), abs=0.5)
    assert abs(gain - 16.99) <= 0.5


@pytest.mark.parametrize("delay", [0.0, 37.0, 159.25, 1000.5])
def test_03_range_compress_peaks_at_delay_times_upsample(delay):
    x = generate_chirp(CFG.waveform).astype(complex)
    f = np.fft.fftfreq(x.size)
    pulse = np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * delay))
    for up in (4, 8):
        y = range_compress(pulse, x, upsample=up)
        assert y.size == x.size * up
        assert int(np.argmax(np.abs(y))) == int(round(delay * up)) % y.size


# --- calibration pieces --------------------------------------------------------------------------


def test_estimate_tau_env_fractional_accuracy():
    scene = sim.SimScene(targets=(sim.SimTarget(37.3, 0, 0.1),), num_sweeps=4, seed=3)
    p = dd.Processor(CFG)
    y = np.concatenate([p.burst_spectra(s.iq) for s in sweeps_of(scene)])
    true = scene.tau_sys_samples + scene.tau_leak_s * FS
    est = cal.estimate_tau_env(y, FS) * FS
    assert abs(est - true) < 0.01, (est, true)
    noisy = dataclasses.replace(scene, noise_std=1500.0)
    y = np.concatenate([p.burst_spectra(s.iq) for s in sweeps_of(noisy)])
    assert abs(cal.estimate_tau_env(y, FS, search_s=(150 / FS, 170 / FS)) * FS - true) < 0.02


def test_estimate_tau_leak_and_separation():
    tau = cal.tau_leak_from_separation(3.0)
    assert tau == pytest.approx(3.0 / C)
    fk = np.array([2426.75e6, 2456.75e6, 2486.75e6])
    ph = np.angle(np.exp(1j * (2 * np.pi * fk * tau + 0.7)))
    assert cal.estimate_tau_leak(ph, fk) == pytest.approx(tau, rel=1e-6)


# --- 4. stationary target, tau_L sign ------------------------------------------------------------


def test_04_stationary_target_range_zero_doppler_and_tau_leak_sign():
    scene = sim.SimScene(targets=(sim.SimTarget(37.3, 0.0, 0.1),), num_sweeps=16, seed=4)
    sw = sweeps_of(scene)
    fitted = dd.fit_leakage_cal(sw, dd.Processor(CFG), scene.tau_leak_s)  # end to end: tau_env fitted
    assert abs(fitted.tau_env_s - true_cal(scene).tau_env_s) * FS < 0.01
    good = dd.Processor(CFG, cal=fitted, pad=8, max_range_m=200)
    m = dd.delay_doppler_map(sw, "hann", processor=good)
    z0 = m.zero_doppler_index
    j, _ = np.unravel_index(np.argmax(np.abs(m.map) * (m.ranges_m > 20)), m.map.shape)
    assert j == z0
    r, _ = dd.peak(m.ranges_m, m.map[z0], 37.3)
    assert abs(r - 37.3) < 0.2
    rp = dd.range_profile(sw[0], good)
    assert abs(dd.peak(rp.ranges_m, rp.profile, 37.3)[0] - 37.3) < 0.2
    # Outline / old apply_tau_leak sign exp(+j 2 pi nu tau_L) == this sign with -tau_L: 34.3 m
    wrong = dd.Processor(CFG, cal=true_cal(scene, -1.0), pad=8, max_range_m=200)
    rp = dd.range_profile(sw[0], wrong)
    assert abs(dd.peak(rp.ranges_m, rp.profile, 34.3)[0] - 34.3) < 0.2
    # No tau_L term at all: range measured from the leakage, 37.3 - 1.5 m
    none = dd.Processor(CFG, cal=cal.LeakageCal(true_cal(scene).tau_env_s, 0.0), pad=8, max_range_m=200)
    rp = dd.range_profile(sw[0], none)
    assert abs(dd.peak(rp.ranges_m, rp.profile, 35.8)[0] - 35.8) < 0.2


def test_apply_tau_leak_formula():
    z = np.ones(1120, complex)
    out = cal.apply_tau_leak(z, -15e6, 1e-8, FS)
    f = np.fft.fftfreq(1120, 1 / FS)
    assert np.allclose(out, np.exp(-2j * np.pi * (-15e6 + f) * 1e-8))


# --- 5-6. LO phase removal, envelope negative control --------------------------------------------


def test_05_random_lo_phase_removed_noise_free():
    t = (sim.SimTarget(37.3, 0.0, 0.1),)
    rnd = sim.SimScene(targets=t, num_sweeps=4, seed=5)
    coh = dataclasses.replace(rnd, random_lo_phase=False)
    p = dd.Processor(CFG, cal=true_cal(rnd), pad=16, max_range_m=200)
    for a, b in zip(sweeps_of(rnd), sweeps_of(coh)):
        ra, rb = dd.range_profile(a, p), dd.range_profile(b, p)
        wa, psl_a, pa, _ = lobe_metrics(ra.ranges_m, ra.profile, 37.3)
        _, _, pb, _ = lobe_metrics(rb.ranges_m, rb.profile, 37.3)
        assert abs(20 * np.log10(pa / pb)) < 0.1
        assert psl_a <= -30.0
    # the LO phase is really there: without the leakage reference the stitch breaks
    raw = dd.Processor(CFG, pad=16, max_range_m=600)
    bad = [lobe_metrics(*dataclasses.astuple(dd.range_profile(s, raw)), 37.3 + 159.3 * C / (2 * FS))[1]
           for s in sweeps_of(rnd)]
    assert max(bad) > -25.0, bad


def test_05b_random_lo_phase_removed_at_20db_snr():
    base = sim.SimScene(targets=(), num_sweeps=16, seed=6, noise_std=1500.0)
    p = dd.Processor(CFG, cal=true_cal(base), pad=8, max_range_m=200)
    floor = np.sqrt(np.mean([np.mean(np.abs(dd.range_profile(s, p).profile[(p.ranges_m > 50)]) ** 2)
                             for s in sweeps_of(base)]))
    amp = 10 * floor  # 20 dB peak SNR in one stitched sweep
    rnd = dataclasses.replace(base, targets=(sim.SimTarget(37.3, 0.0, amp),))
    coh = dataclasses.replace(rnd, random_lo_phase=False)
    pk_r = [dd.peak(*dataclasses.astuple(dd.range_profile(s, p)), 37.3, 2.0)[1] for s in sweeps_of(rnd)]
    pk_c = [dd.peak(*dataclasses.astuple(dd.range_profile(s, p)), 37.3, 2.0)[1] for s in sweeps_of(coh)]
    snr = 20 * np.log10(np.mean(pk_c) / floor)
    assert 18.0 < snr < 22.0
    assert abs(20 * np.log10(np.mean(pk_r) / np.mean(pk_c))) < 1.0


def test_06_negative_control_integer_tau_env_raises_psl():
    scene = sim.SimScene(targets=(sim.SimTarget(37.3, 0.0, 0.1),), num_sweeps=1, seed=7)
    sw = sweeps_of(scene)[0]
    exact = true_cal(scene)
    rounded = cal.LeakageCal(round(exact.tau_env_s * FS) / FS, exact.tau_leak_s)
    assert abs(exact.tau_env_s * FS - rounded.tau_env_s * FS) > 0.1  # 159.86 -> 160 samples
    psl = []
    for c in (exact, rounded):
        rp = dd.range_profile(sw, dd.Processor(CFG, cal=c, pad=16, max_range_m=200))
        psl.append(lobe_metrics(rp.ranges_m, rp.profile, 37.3)[1])
    assert psl[1] - psl[0] > 10.0, psl


# --- 7-8. resolution ------------------------------------------------------------------------------


def test_07_width_stitched_vs_one_step():
    scene = sim.SimScene(targets=(sim.SimTarget(37.3, 0.0, 0.1),), num_sweeps=1, seed=8)
    sw = sweeps_of(scene)[0]
    stitched = dd.Processor(CFG, cal=true_cal(scene), pad=16, max_range_m=200)
    single = dd.Processor(CFG, cal=true_cal(scene), pad=16, max_range_m=200, steps=[0])
    w2 = lobe_metrics(*dataclasses.astuple(dd.range_profile(sw, stitched)), 37.3)[0]
    w1 = lobe_metrics(*dataclasses.astuple(dd.range_profile(sw, single)), 37.3)[0]
    assert w2 == pytest.approx(3.00, rel=0.05)
    assert w1 == pytest.approx(5.08, rel=0.05)
    assert stitched.range_resolution_m == pytest.approx(C / (2 * 75e6))
    assert single.range_resolution_m == pytest.approx(C / (2 * 45e6))


@pytest.mark.parametrize("window,sep", [("rect", 3.2), ("hann", 5.0)])
def test_08_two_targets_resolved_at_all_phases(window, sep):
    r1 = 60.0
    stitched_dips, single_dips = [], []
    for th in np.linspace(0, 2 * np.pi, 97):
        scene = sim.SimScene(targets=(sim.SimTarget(r1, 0.0, 0.3), sim.SimTarget(r1 + sep, 0.0, 0.3 * np.exp(1j * th))),
                             num_sweeps=1, seed=9, leakage_amplitude=0.3)
        sw = sweeps_of(scene)[0]
        for steps, out in ((None, stitched_dips), ([0], single_dips)):
            p = dd.Processor(CFG, cal=true_cal(scene), pad=16, max_range_m=200, range_window=window, steps=steps)
            rp = dd.range_profile(sw, p)
            out.append(dip_db(rp.ranges_m, rp.profile, r1, r1 + sep))
    assert min(stitched_dips) >= 1.5, stitched_dips
    assert min(single_dips) < 1.5, single_dips


# --- 9-10. moving target --------------------------------------------------------------------------


def _moving_target_check(times=None):
    m = 64
    v = 0.5
    kw = {} if times is None else {"burst_times_s": times}
    moving = sim.SimScene(targets=(sim.SimTarget(37.3, v, 0.1),), num_sweeps=m, seed=10, **kw)
    static = dataclasses.replace(moving, targets=(sim.SimTarget(37.3, 0.0, 0.1),))
    p = dd.Processor(CFG, cal=true_cal(moving), pad=8, max_range_m=200)
    mm = dd.delay_doppler_map(sweeps_of(moving), "hann", processor=p)
    ms = dd.delay_doppler_map(sweeps_of(static), "hann", processor=p)
    far = mm.ranges_m > 20
    j, i = np.unravel_index(np.argmax(np.abs(mm.map) * far), mm.map.shape)
    expected = 2 * v * m * mm.sweep_period_s / LAM
    assert abs((j - mm.zero_doppler_index) - expected) <= 0.5, (j - mm.zero_doppler_index, expected)
    ref = np.abs(ms.map[ms.zero_doppler_index][far]).max()
    loss = 20 * np.log10(np.abs(mm.map[j, i]) / ref)
    assert loss > -0.5, loss
    r_mid = 37.3 - v * (mm.t_mid_s - sim.burst_times(CFG, moving)[0, 0])
    assert abs(mm.ranges_m[i] - r_mid) < 0.5
    return loss, mm


def test_09_moving_target_doppler_bin_and_loss():
    _moving_target_check()


@pytest.mark.skipif(not (RUN / "bursts.csv").exists(), reason="real run folder not on this machine")
def test_10_moving_target_with_real_timestamps():
    with open_source(RUN) as src:
        b = src.bursts[:128]
    assert (b["step"] == np.tile([0, 1], 64)).all()
    times = b["usrp_time_s"].reshape(64, 2)
    loss, mm = _moving_target_check(times)
    assert mm.sweep_period_s == pytest.approx(30.64e-3, rel=0.02)


# --- 11. leakage at zero Doppler, DC offset ------------------------------------------------------


def test_11_leakage_zero_doppler_and_dc_offset():
    base = sim.SimScene(num_sweeps=32, seed=11, noise_std=2000.0)
    dc = dataclasses.replace(base, dc_offset=3000.0)
    p = dd.Processor(CFG, cal=true_cal(base), pad=8, max_range_m=200)
    m0 = dd.delay_doppler_map(sweeps_of(base), processor=p)
    j, i = np.unravel_index(np.argmax(np.abs(m0.map)), m0.map.shape)
    assert j == m0.zero_doppler_index
    assert abs(dd.peak(m0.ranges_m, m0.map[j], 1.5, 2.0)[0] - 1.5) < 0.2
    m1 = dd.delay_doppler_map(sweeps_of(dc), processor=p)
    off = np.ones(m0.map.shape[0], bool)
    off[m0.zero_doppler_index - 1 : m0.zero_doppler_index + 2] = False
    rng = m0.ranges_m > 10
    n0 = np.mean(np.abs(m0.map[off][:, rng]) ** 2)
    n1 = np.mean(np.abs(m1.map[off][:, rng]) ** 2)
    assert 10 * np.log10(n1 / n0) <= 1.0
    # static-clutter removal takes the leakage out of the zero-Doppler line
    m2 = dd.delay_doppler_map(sweeps_of(base), processor=p, remove_static=True)
    assert np.abs(m2.map[m2.zero_doppler_index, np.abs(m2.ranges_m - 1.5) < 1]).max() < 0.1


# --- 12. config checks, N = 3 ---------------------------------------------------------------------


def test_12_non_integer_spacing_raises():
    with pytest.raises(ValueError, match="multiple"):
        dd.Processor(cfg_with(step_spacing_hz=30.01e6))


def test_12_three_steps_at_15mhz():
    cfg3 = cfg_with(num_steps=3, step_spacing_hz=15e6)
    scene = sim.SimScene(targets=(sim.SimTarget(37.3, 0.0, 0.1),), num_sweeps=4, seed=12)
    sw = sim.run_sweeps(sim.simulate_run(cfg3, scene))
    p = dd.Processor(cfg3, cal=true_cal(scene), pad=16, max_range_m=200)
    m = dd.delay_doppler_map(sw, processor=p)
    assert abs(dd.peak(m.ranges_m, m.map[m.zero_doppler_index], 37.3)[0] - 37.3) < 0.2


def test_missing_burst_normalises_per_step():
    scene = sim.SimScene(targets=(sim.SimTarget(37.3, 0.0, 0.1),), num_sweeps=8, seed=14)
    p = dd.Processor(CFG, cal=true_cal(scene), pad=8, max_range_m=200)
    full = dd.delay_doppler_map(sweeps_of(scene), "rect", processor=p)
    sw = sweeps_of(scene)
    s = sw[3]
    sw[3] = dataclasses.replace(s, iq=np.where(np.array([True, False]).reshape((-1,) + (1,) * (s.iq.ndim - 1)), s.iq, 0), valid=np.array([True, False]), present=np.array([True, False]),
                                usrp_ticks=np.array([s.usrp_ticks[0], -1]))
    gap = dd.delay_doppler_map(sw, "rect", processor=p)
    a = np.abs(full.map[full.zero_doppler_index]).max()
    b = np.abs(gap.map[gap.zero_doppler_index]).max()
    assert 20 * np.log10(b / a) == pytest.approx(0.0, abs=0.3)  # w.sum() over all sweeps would give -1.2 dB
    rp = dd.range_profile(sw[0], p)
    w, psl, _, r = lobe_metrics(rp.ranges_m, rp.profile, 37.3)
    assert abs(r - 37.3) < 0.2
    assert w == pytest.approx(3.00, rel=0.05)
    assert psl <= -30.0


# --- derived numbers, round trip, structure -------------------------------------------------------


def test_derived_numbers_match_budget_measured_and_planned(tmp_path):
    m = 32
    scene = sim.SimScene(targets=(sim.SimTarget(20.0, 0.0, 0.1),), num_sweeps=m, seed=13)
    sim.simulate_run(CFG, scene, tmp_path / "run")
    with open_source(tmp_path / "run") as src:
        assert src.report().ok
        sweeps = list(src.iter_sweeps())
    p = dd.Processor(CFG, cal=true_cal(scene))
    mp = dd.delay_doppler_map(sweeps, processor=p)
    meas = budget.measured_sweep_period(tmp_path / "run")
    assert mp.sweep_period_s == pytest.approx(meas["sweep_period_s"], rel=1e-9)
    d = budget.delay_doppler(CFG, m, meas["sweep_period_s"])
    assert d.basis == "measured"
    assert p.range_resolution_m == pytest.approx(d.range_resolution_m)
    assert p.total_bandwidth_hz == d.total_bandwidth_hz
    assert dd.Processor(CFG, steps=[1]).range_resolution_m == pytest.approx(d.range_resolution_single_step_m)
    assert p.max_unambiguous_range_m == pytest.approx(d.max_unambiguous_range_m)
    assert mp.doppler_resolution_hz == pytest.approx(d.doppler_resolution_hz)
    assert np.diff(mp.doppler_hz) == pytest.approx(d.doppler_resolution_hz)
    assert -mp.doppler_hz[0] == pytest.approx(d.doppler_span_hz)
    assert mp.velocity_resolution_mps == pytest.approx(d.velocity_resolution_mps)
    assert mp.velocity_span_mps == pytest.approx(d.velocity_span_mps)
    assert mp.velocity_span_mps == pytest.approx(1.00, abs=0.02)
    assert abs(dd.peak(mp.ranges_m, mp.map[mp.zero_doppler_index], 20.0)[0] - 20.0) < 0.2
    # planned basis: a sim timed exactly at the budget's sweep reproduces the planned numbers
    planned = budget.delay_doppler(CFG, m)
    assert planned.basis == "planned"
    ps = dataclasses.replace(scene, step_period_s=planned.sweep_period_s / 2, timing_jitter_s=0.0)
    mq = dd.delay_doppler_map(sweeps_of(ps), processor=p)
    assert mq.sweep_period_s == pytest.approx(planned.sweep_period_s, rel=1e-6)
    assert mq.velocity_span_mps == pytest.approx(planned.velocity_span_mps, rel=1e-6)
    assert mq.velocity_resolution_mps == pytest.approx(planned.velocity_resolution_mps, rel=1e-6)


def test_missing_burst_is_skipped():
    scene = sim.SimScene(targets=(sim.SimTarget(37.3, 0.0, 0.1),), num_sweeps=8, seed=14)
    sw = sweeps_of(scene)
    s = sw[3]
    sw[3] = dataclasses.replace(s, iq=s.iq * 0, valid=np.array([True, False]), present=np.array([True, False]),
                                usrp_ticks=np.array([s.usrp_ticks[0], -1]))
    p = dd.Processor(CFG, cal=true_cal(scene), pad=8, max_range_m=200)
    m = dd.delay_doppler_map(sw, processor=p)
    assert m.missing_bursts == 1
    assert abs(dd.peak(m.ranges_m, m.map[m.zero_doppler_index], 37.3)[0] - 37.3) < 0.2


def test_rx_only_processing_without_leakage_reference():
    scene = sim.SimScene(num_sweeps=4, seed=15, leakage_path_m=None, noise_std=50.0)
    p = dd.Processor(CFG)  # no cal: c = 1
    m = dd.delay_doppler_map(sweeps_of(scene), processor=p)
    assert m.map.shape == (4, p.ranges_m.size) and np.isfinite(m.map).all()


def test_processing_path_imports_without_scipy():
    code = ("import sys; sys.modules['scipy'] = None\n"
            "import b210_sar.chirp, b210_sar.sim, b210_sar.processing.pulse, "
            "b210_sar.processing.calibration, b210_sar.processing.delay_doppler\n")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_dc_bin_interpolated_not_a_hole():
    """Deviation from the memo (zeroed DC): a zeroed bin leaves a flat floor ~-55 dB under the leakage."""
    scene = sim.SimScene(num_sweeps=1, seed=16)
    sw = sweeps_of(scene)[0]
    floors = {}
    for mode in ("interpolate", "zero"):
        rp = dd.range_profile(sw, dd.Processor(CFG, cal=true_cal(scene), pad=16, max_range_m=200, dc=mode))
        a = np.abs(rp.profile)
        floors[mode] = 20 * np.log10(a[(rp.ranges_m > 27) & (rp.ranges_m < 47)].max() / a.max())
    assert floors["interpolate"] < -70.0, floors
    assert floors["zero"] > -60.0, floors
