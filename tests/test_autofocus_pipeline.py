"""b210-31 Step 3: prominent-point autofocus and the sar-image pipeline (simulated, first-experiment geometry).

Positions come from wheel odometry only (no IMU), so the tests feed the image former positions with a
known error while the simulator uses the truth. A sinusoidal cross-track error of amplitude a gives a
round-trip phase error of amplitude beta = 4 pi a / lambda and, over many periods, a peak loss of
20 log10 J0(beta): -2.4 dB at 1 cm, so the ">= 3 dB" case uses 1.5 cm (-6.0 dB); 1 cm is checked
against the J0 prediction.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from scipy.special import j0

from b210_sar import sim
from b210_sar.positions import AntennaPositions, write_positions
from b210_sar.processing.autofocus import prominent_point_autofocus
from b210_sar.processing.backprojection import ImageGrid, backproject
from b210_sar.processing.pipeline import main, process_run, PipelineOptions

from test_backprojection import D, LAM, SMALL, TARGET, ideal, lobe, scene

GRID = ImageGrid(np.round(np.arange(-0.4, 0.4001, 0.01), 6), np.round(np.arange(-8.0, -4.0001, 0.05), 6))


def _db(a, b):
    return 20 * np.log10(a / b)


@pytest.fixture(scope="module")
def pass_2p5():
    p, z, steps, pos, sc = ideal(2.5)
    prof = p.step_profiles(z, steps)
    ref = np.abs(backproject(prof, p.ranges_m, p.reference_freq_hz, pos, GRID)).max()
    return p, prof, sc, ref


def _focus(pass_2p5, tx, rx):
    p, prof, sc, ref = pass_2p5
    q = AntennaPositions(tx.reshape(-1, 3), rx.reshape(-1, 3))
    img = backproject(prof, p.ranges_m, p.reference_freq_hz, q, GRID)
    af = prominent_point_autofocus(prof, p.ranges_m, p.reference_freq_hz, q, GRID,
                                   times_s=sc.burst_times_s.reshape(-1), image=img)
    return _db(np.abs(img).max(), ref), _db(np.abs(af.image).max(), ref), img, af


@pytest.mark.parametrize("amp_m, period_m", [(0.01, 0.25), (0.015, 0.25), (0.015, 1.0)])
def test_sinusoidal_cross_track_error(pass_2p5, amp_m, period_m):
    sc = pass_2p5[2]
    tx, rx = sim.odometry_positions(sc.tx_xyz, sc.rx_xyz, lateral_m=(amp_m, period_m))
    before, after, _, af = _focus(pass_2p5, tx, rx)
    predicted = 20 * np.log10(j0(4 * np.pi * amp_m / LAM))
    print(f"sinusoid {amp_m * 100:.1f} cm / {period_m} m: before {before:.2f} dB (J0 {predicted:.2f}), "
          f"after {after:.2f} dB, {af.iterations} iterations, rms {np.round(af.rms_update_rad, 3).tolist()}")
    if period_m < 0.5:  # many periods over the pass: the J0 prediction holds
        assert abs(before - predicted) < 0.5
    if amp_m >= 0.015:
        assert before <= -3.0
    assert after >= -1.0
    assert af.iterations <= 5


def test_odometry_like_error(pass_2p5):
    """Wheel odometry: 3 % along-track scale error, 4 deg heading drift over the pass, 5 mm wheel wobble."""
    p, prof, sc, ref = pass_2p5
    tx, rx = sim.odometry_positions(sc.tx_xyz, sc.rx_xyz, scale_error=0.03, heading_drift_rad=np.deg2rad(4.0),
                                    lateral_m=(0.005, 0.3))
    before, after, img, af = _focus(pass_2p5, tx, rx)
    mid = 0.5 * (sc.tx_xyz + sc.rx_xyz)
    theory = LAM * 6 / (2 * (mid[..., 0].max() - mid[..., 0].min()))
    a = np.abs(img)
    iy, _ = np.unravel_index(int(np.argmax(a)), a.shape)
    w_before = lobe(GRID.x_m, img[iy], 0.3)[0]
    iy2 = int(np.argmin(np.abs(GRID.y_m - af.peak_xy[1])))
    w_after = lobe(GRID.x_m, af.image[iy2], 0.3)[0]
    print(f"odometry-like: before {before:.2f} dB width {w_before:.3f} m at peak "
          f"({GRID.x_m[np.argmax(a[iy])]:.2f}, {GRID.y_m[iy]:.2f}); after {after:.2f} dB width {w_after:.3f} m "
          f"at {af.peak_xy}; theory {theory:.3f} m; rms {np.round(af.rms_update_rad, 3).tolist()}")
    assert before <= -1.5
    assert after >= -0.5
    assert abs(w_after / theory - 1) < 0.25
    # autofocus removes defocus, not the geolocation error: the peak stays within 0.5 m of the truth
    assert abs(af.peak_xy[0] - TARGET[0]) < 0.5 and abs(af.peak_xy[1] - TARGET[1]) < 0.5


@pytest.fixture(scope="module")
def sim_run(tmp_path_factory):
    """A written sim run (leakage, random LO phases, timing jitter) plus true and odometry-like positions."""
    d = tmp_path_factory.mktemp("sar_run")
    sc = scene(2.5)
    run = sim.simulate_run(SMALL, sc, d / "run")
    idx = run.bursts["index"]
    write_positions(d / "true.csv", idx, sc.tx_xyz.reshape(-1, 3), sc.rx_xyz.reshape(-1, 3))
    tx, rx = sim.odometry_positions(sc.tx_xyz, sc.rx_xyz, scale_error=0.03, heading_drift_rad=np.deg2rad(4.0),
                                    lateral_m=(0.005, 0.3))
    write_positions(d / "run" / "antenna_positions.csv", idx, tx.reshape(-1, 3), rx.reshape(-1, 3))
    return d


def test_cli_end_to_end(sim_run, capsys):
    d = sim_run
    grid = ["--x", "-0.4", "0.4", "0.01", "--y", "-8", "-4", "0.05"]
    assert main([str(d / "run"), str(d / "af"), *grid]) == 0  # odometry positions from the run folder
    assert main([str(d / "run"), str(d / "noaf"), "--no-autofocus", *grid]) == 0
    assert main([str(d / "run"), str(d / "truth"), "--no-autofocus", "--positions", str(d / "true.csv"), *grid]) == 0
    print(capsys.readouterr().out)
    res = {k: np.load(d / k / "image.npz") for k in ("af", "noaf", "truth")}
    pk = {k: np.abs(v["image"]).max() for k, v in res.items()}
    truth = res["truth"]
    a = np.abs(truth["image"])
    iy, ix = np.unravel_index(int(np.argmax(a)), a.shape)
    assert abs(truth["x_m"][ix] - TARGET[0]) <= 0.01 + 1e-9 and abs(truth["y_m"][iy] - TARGET[1]) <= 0.05 + 1e-9
    assert 0.08 < pk["truth"] < 0.105  # a / a_L = 0.1 less the clutter-removal loss
    print(f"CLI: truth {pk['truth']:.4f}, odometry no AF {_db(pk['noaf'], pk['truth']):.2f} dB, "
          f"odometry AF {_db(pk['af'], pk['truth']):.2f} dB")
    assert _db(pk["noaf"], pk["truth"]) <= -1.5
    assert _db(pk["af"], pk["truth"]) >= -1.0
    # outputs
    af = res["af"]
    assert af["subband_images"].shape == (2,) + af["image"].shape
    assert af["autofocus_phase_rad"].shape == af["burst_index"].shape
    opts = json.loads(str(af["options"]))
    assert opts["autofocus"] is True and opts["remove_clutter_applied"] is True
    import hashlib

    assert str(af["positions_sha256"]) == hashlib.sha256((d / "run" / "antenna_positions.csv").read_bytes()).hexdigest()
    for name in ("image.png", "subband_0.png", "subband_1.png"):
        assert (d / "af" / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_positions_separation_mismatch_fails_loudly(sim_run, tmp_path):
    d = sim_run
    from b210_sar.dataset import load_run

    idx = load_run(d / "run").bursts["index"]
    bad = tmp_path / "bad.csv"
    tx = np.zeros((idx.size, 3))
    rx = tx + np.array([-(D + 0.05), 0, 0])
    write_positions(bad, idx, tx, rx)
    with pytest.raises(ValueError, match="tx_rx_separation_m"):
        process_run(d / "run", tmp_path / "out", PipelineOptions(grid=GRID, positions_path=bad))
