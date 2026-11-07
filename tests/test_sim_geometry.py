"""b210-31 Step 1: geometric simulator scenes and the antenna positions file."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from b210_sar import sim
from b210_sar.config import config_dir, load_config
from b210_sar.positions import PositionsError, load_positions, write_positions

CFG = load_config(config_dir() / "sar_first_2x40.yaml")
# 2 chirps per dwell instead of 50: identical noise-free spectra, 25x less memory
SMALL = dataclasses.replace(CFG, waveform=dataclasses.replace(CFG.waveform, dwell_s=4e-5))
D = CFG.antenna.tx_rx_separation_m


def test_geometric_scene_matches_range_only_when_parked():
    """Parked antennas: an xyz target at bistatic path 2R gives the same IQ as a range-only target at R."""
    tx = np.array([D / 2, 0.0, 0.0])
    rx = np.array([-D / 2, 0.0, 0.0])
    x = np.array([0.0, -6.0, 0.0])
    r = 0.5 * (np.linalg.norm(tx - x) + np.linalg.norm(rx - x))
    m, n = 3, CFG.waveform.num_steps
    kw = dict(num_sweeps=m, seed=2, leakage_path_m=D)
    ref = sim.SimScene(targets=(sim.SimTarget(r, 0.0, 0.1),), **kw)
    geo = sim.SimScene(targets=(sim.SimTarget(xyz_m=tuple(x), amplitude=0.1),),
                       tx_xyz=np.broadcast_to(tx, (m, n, 3)).copy(), rx_xyz=np.broadcast_to(rx, (m, n, 3)).copy(), **kw)
    a, ta = sim.simulate_bursts(SMALL, ref)
    b, tb = sim.simulate_bursts(SMALL, geo)
    assert np.array_equal(ta, tb)
    assert np.max(np.abs(a - b)) < 1e-6 * np.max(np.abs(a))


def test_target_needs_one_of_range_and_xyz():
    with pytest.raises(ValueError):
        sim.SimTarget()
    with pytest.raises(ValueError):
        sim.SimTarget(6.0, xyz_m=(0, -6, 0))
    with pytest.raises(ValueError):
        sim.simulate_bursts(SMALL, sim.SimScene(targets=(sim.SimTarget(xyz_m=(0, -6, 0)),), num_sweeps=1))


def test_straight_pass_positions_and_times():
    v = 0.15
    sc = sim.straight_pass(SMALL, (-1.25, 0, 0), (v, 0, 0), (D / 2, 0, 0), (-D / 2, 0, 0), num_sweeps=20, seed=1)
    t = sc.burst_times_s
    assert sc.tx_xyz.shape == sc.rx_xyz.shape == (20, 2, 3)
    assert sc.leakage_path_m == pytest.approx(D)
    assert np.allclose(np.linalg.norm(sc.tx_xyz - sc.rx_xyz, axis=-1), D)
    mid = 0.5 * (sc.tx_xyz + sc.rx_xyz)
    assert np.allclose(mid[..., 0], -1.25 + v * (t - t[0, 0]))
    assert np.all(sc.tx_xyz[..., 0] > sc.rx_xyz[..., 0])  # TX ahead along-track
    # the stored times are the ones the simulator uses
    assert np.array_equal(sim.burst_times(SMALL, sc), t)


def test_odometry_positions_error_model():
    sc = sim.straight_pass(SMALL, (-1.25, 0, 0), (0.15, 0, 0), (D / 2, 0, 0), (-D / 2, 0, 0), num_sweeps=400, seed=1)
    tx, rx = sim.odometry_positions(sc.tx_xyz, sc.rx_xyz, scale_error=0.02, heading_drift_rad=np.deg2rad(2),
                                    lateral_m=(0.005, 1.0))
    assert np.allclose(np.linalg.norm(tx - rx, axis=-1), D)  # rigid offset unchanged
    err = 0.5 * (tx + rx) - 0.5 * (sc.tx_xyz + sc.rx_xyz)
    s = 0.5 * (sc.tx_xyz + sc.rx_xyz)[..., 0] + 1.25
    L = s.max()
    assert np.allclose(err[..., 0], 0.02 * s)
    assert np.allclose(err[..., 1], np.deg2rad(2) * s ** 2 / (2 * L) + 0.005 * np.sin(2 * np.pi * s))
    assert np.allclose(err[..., 2], 0)


def _write(tmp_path, idx, tx, rx, name="antenna_positions.csv"):
    p = tmp_path / name
    write_positions(p, idx, tx, rx)
    return p


def test_load_positions_roundtrip_order_and_checks(tmp_path):
    rng = np.random.default_rng(0)
    idx = np.arange(10)
    tx = rng.normal(size=(10, 3))
    rx = tx + np.array([-D, 0, 0])
    p = _write(tmp_path, idx, tx, rx)
    want = np.array([7, 2, 9])
    pos = load_positions(p, want, D)
    assert np.array_equal(pos.tx, tx[want]) and np.array_equal(pos.rx, rx[want])
    with pytest.raises(PositionsError, match="no row"):
        load_positions(p, np.array([3, 10]), D)
    with pytest.raises(PositionsError, match="tx_rx_separation_m"):
        load_positions(p, idx, D + 0.011)
    load_positions(p, idx, D + 0.009)  # within 1 cm
    p2 = _write(tmp_path, np.r_[idx, 3], np.r_[tx, tx[:1]], np.r_[rx, rx[:1]], "dup.csv")
    with pytest.raises(PositionsError, match="duplicate"):
        load_positions(p2, idx, D)
    p3 = tmp_path / "bad.csv"
    p3.write_text("index,x,y\n0,1,2\n")
    with pytest.raises(PositionsError, match="header"):
        load_positions(p3, idx[:1], D)
    tx_nan = tx.copy()
    tx_nan[4, 1] = np.nan
    p4 = _write(tmp_path, idx, tx_nan, rx, "nan.csv")
    with pytest.raises(PositionsError, match="non-finite"):
        load_positions(p4, idx, D)
