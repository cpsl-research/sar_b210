import dataclasses
from pathlib import Path

import numpy as np
import pytest
import yaml

from b210_sar import dataset
from b210_sar.config import config_dir, parse_config
from b210_sar.dataset import BURST_DTYPE, Run, open_source, write_run

NUM_STEPS, NS = 4, 8


def make_run(sweeps=3, drop=(), invalid=(), config_text=True) -> Run:
    raw = yaml.safe_load((config_dir() / "default.yaml").read_text())
    raw["waveform"]["num_steps"] = NUM_STEPS
    cfg, issues = parse_config(raw)
    assert cfg is not None, issues
    rows, iq, off = [], [], 0
    for sw in range(sweeps):
        for st in range(NUM_STEPS):
            if (sw, st) in drop:
                continue
            k = len(rows)
            row = np.zeros((), BURST_DTYPE)
            row["index"], row["sweep"], row["step"] = k, sw, st
            row["sample_offset"], row["num_samples"] = off, NS
            row["usrp_ticks"] = 1000 * k
            row["usrp_time_s"] = 0.01 * k
            row["tx_freq_hz"] = row["rx_freq_hz"] = 2.4e9 + 3e7 * st
            row["valid"] = (sw, st) not in invalid
            rows.append(row)
            iq.append(((np.arange(2 * NS) + 100 * k) % 3000 - 1500).astype("<i2"))
            off += NS
    cs = np.zeros(2, dataset.CLOCK_SYNC_DTYPE)
    cs["usrp_ticks"] = [5, 6]
    return Run(None, cfg, np.array(rows), np.concatenate(iq), np.ones(5, np.complex64), cs,
               config_text=yaml.safe_dump(raw) if config_text else "")


def expected(run, sw, st):
    r = int(np.flatnonzero((run.bursts["sweep"] == sw) & (run.bursts["step"] == st))[0])
    o = int(run.bursts["sample_offset"][r])
    a = run.iq[2 * o:2 * (o + NS)].astype(np.float32)
    return a[0::2] + 1j * a[1::2]


def test_roundtrip_run_folder(tmp_path):
    run = make_run()
    write_run(tmp_path / "r", run)
    src = open_source(tmp_path / "r")
    assert np.array_equal(src.bursts, run.bursts)
    assert src.config == run.config
    assert src.report().ok and src.report().num_sweeps == 3 and src.report().complete_sweeps == 3
    assert isinstance(src.iq, np.memmap)
    sweeps = list(src.iter_sweeps())
    assert [s.sweep for s in sweeps] == [0, 1, 2]
    s1 = src.sweep(1)
    assert s1.iq.shape == (NUM_STEPS, NS) and s1.iq.dtype == np.complex64
    for st in range(NUM_STEPS):
        assert np.array_equal(s1.iq[st], expected(run, 1, st))
    assert s1.complete and s1.valid.all()
    assert np.allclose(s1.tx_freq_hz, 2.4e9 + 3e7 * np.arange(NUM_STEPS))
    assert list(s1.usrp_ticks) == [1000 * (NUM_STEPS + k) for k in range(NUM_STEPS)]
    assert np.array_equal(src.clock_sync["usrp_ticks"], [5, 6])
    assert dataset.load_run(tmp_path / "r").replica.shape == (5,)
    assert np.array_equal(dataset.burst_iq(dataset.load_run(tmp_path / "r"), 5), expected(run, 1, 1))


def test_missing_and_invalid_reported(tmp_path):
    run = make_run(drop={(1, 2)}, invalid={(2, 0)})
    write_run(tmp_path / "r", run)
    src = open_source(tmp_path / "r")
    rep = src.report()
    assert rep.incomplete == {1: [2]} and rep.complete_sweeps == 2 and not rep.ok
    assert len(rep.invalid_bursts) == 1
    s = src.sweep(1)
    assert not s.complete and s.missing_steps == [2] and not s.present[2] and not s.valid[2]
    assert np.isnan(s.tx_freq_hz[2]) and s.usrp_ticks[2] == -1 and not s.iq[2].any()
    assert s.present[[0, 1, 3]].all()
    s2 = src.sweep(2)
    assert s2.complete and list(s2.valid) == [False, True, True, True]
    assert len(list(src.iter_sweeps())) == 3  # incomplete sweep still yielded


def test_duplicate_unexpected_and_truncated(tmp_path):
    run = make_run(sweeps=1)
    b = run.bursts.copy()
    b["step"][3] = 1  # duplicate (0,1), and step 3 now absent
    b["step"][2] = NUM_STEPS + 2  # unexpected
    write_run(tmp_path / "r", dataclasses.replace(run, bursts=b, iq=run.iq[:-2 * NS]))
    rep = open_source(tmp_path / "r").report()
    assert rep.duplicates == [(0, 1)] and rep.unexpected == [(0, NUM_STEPS + 2)]
    assert rep.truncated == [3] and sorted(rep.incomplete[0]) == [2, 3]


h5py = pytest.importorskip("h5py")


def write_h5(path, run, iq=True, run_dir="", name="b210_bursts", config=True, clock=None):
    with h5py.File(path, "w") as f:
        g = f.create_group(name)
        g.attrs["msg_type"] = "sar_b210_msgs/msg/SarBurst"
        g.attrs["b210_run_dir"] = run_dir
        g.attrs["b210_config_status"] = "ok" if config else "not_received"
        g.attrs["b210_config_text"] = run.config_text if config else ""
        for c in dataset.BURST_COLUMNS:
            g[c] = run.bursts[c].astype({"index": "u8", "sample_offset": "u8", "num_samples": "u8",
                                         "sweep": "u4", "step": "u4", "error_flags": "u4"}.get(c, run.bursts[c].dtype))
        if iq:
            g["iq"] = np.stack([run.iq[2 * int(o):2 * (int(o) + NS)] for o in run.bursts["sample_offset"]])
        cname = clock or name.replace("bursts", "clock_sync")
        c = f.create_group(cname)
        c.attrs["msg_type"] = "sar_b210_msgs/msg/SarClockSync"
        c["data"] = np.array([[5, 1, 2, 3], [6, 1, 2, 3]], dtype="i8")


def test_h5_matches_run_folder(tmp_path):
    run = make_run(drop={(1, 2)}, invalid={(2, 0)})
    write_run(tmp_path / "r", run)
    write_h5(tmp_path / "a.h5", run)
    a, b = open_source(tmp_path / "r"), open_source(tmp_path / "a.h5")
    assert b.iq_source == "hdf5" and b.config == a.config and b.stream == "b210_bursts"
    assert np.array_equal(a.bursts, b.bursts)
    assert a.report().summary().split(";")[:-1] == b.report().summary().split(";")[:-1]
    for sa, sb in zip(a.iter_sweeps(), b.iter_sweeps()):
        assert np.array_equal(sa.iq, sb.iq) and np.array_equal(sa.valid, sb.valid)
        assert np.array_equal(sa.present, sb.present)
    assert np.array_equal(b.clock_sync["usrp_ticks"], [5, 6])
    b.close()


def test_h5_iq_absent_falls_back_to_run_dir(tmp_path):
    run = make_run()
    write_run(tmp_path / "r", run)
    write_h5(tmp_path / "a.h5", run, iq=False, run_dir=str(tmp_path / "r"))
    src = open_source(tmp_path / "a.h5")
    assert src.iq_source == "run_dir" and src.report().ok
    for st in range(NUM_STEPS):
        assert np.array_equal(src.sweep(2).iq[st], expected(run, 2, st))
    src.close()


def test_h5_iq_absent_without_run_dir_is_reported(tmp_path):
    run = make_run(sweeps=1)
    write_h5(tmp_path / "a.h5", run, iq=False, run_dir=str(tmp_path / "gone"))
    src = open_source(tmp_path / "a.h5")
    rep = src.report()
    assert src.iq_source == "none" and len(rep.truncated) == len(run.bursts) and 0 in rep.incomplete
    assert len(src.bursts) == len(run.bursts)  # metadata still usable
    assert not src.sweep(0).present.any()


def test_h5_device_prefixed_stream_by_msg_type(tmp_path):
    run = make_run(sweeps=1)
    write_h5(tmp_path / "a.h5", run, name="rx2_bursts")
    src = open_source(tmp_path / "a.h5")
    assert src.stream == "rx2_bursts" and np.array_equal(src.clock_sync["usrp_ticks"], [5, 6])
    two = tmp_path / "two.h5"
    write_h5(two, run, name="a_bursts")
    with h5py.File(two, "a") as f:
        f.copy("a_bursts", "b_bursts")
    with pytest.raises(ValueError, match="several"):
        open_source(two)
    assert open_source(two, stream="b_bursts").stream == "b_bursts"


def test_h5_without_config_uses_observed_steps(tmp_path):
    run = make_run(sweeps=2)
    write_h5(tmp_path / "a.h5", run, config=False)
    src = open_source(tmp_path / "a.h5")
    assert src.config is None and src.report().expected_steps == NUM_STEPS and not src.report().config_known


def test_not_a_run(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        open_source(tmp_path / "empty")
