"""File view API: sim fixture -> served frame, path confinement, report visibility."""

import dataclasses
import os
import struct
import json

import numpy as np
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from b210_sar import sim  # noqa: E402
from b210_sar.config import config_dir, load_config  # noqa: E402
from b210_sar.gui.app import create_app  # noqa: E402

CFG = load_config(config_dir() / "default.yaml")
RANGE, VEL = 37.3, 0.5


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("data")
    scene = sim.SimScene(targets=(sim.SimTarget(RANGE, VEL, 0.1),), num_sweeps=70, seed=11)
    sim.simulate_run(CFG, scene, root / "sim_run")
    # a run with one burst dropped (incomplete last sweep): the report must say so
    run = sim.simulate_run(CFG, dataclasses.replace(scene, num_sweeps=66))
    return root, run


@pytest.fixture()
def client(runs, tmp_path):
    root, _ = runs
    with TestClient(create_app(data_roots=[root])) as c:
        yield c


def read_map(ws):
    raw = ws.receive_bytes()
    n = struct.unpack("<I", raw[:4])[0]
    hdr = json.loads(raw[4:4 + n])
    db = np.frombuffer(raw[4 + n:], dtype="<f4").reshape(hdr["shape"])
    return hdr, db


def test_target_at_its_range_and_velocity(client, runs):
    root, _ = runs
    with client.websocket_connect("/dd") as ws:
        r = client.post("/api/open", json={"path": str(root / "sim_run"), "m": 64, "leakage_m": 3.0})
        assert r.status_code == 200, r.text
        hdr, db = read_map(ws)
    assert hdr["shape"][0] == 64 and hdr["pos"] == 63
    rng = np.linspace(hdr["ranges_m"][0], hdr["ranges_m"][1], hdr["shape"][1])
    v = np.array(hdr["velocity_mps"])
    far = rng > 20
    j, i = np.unravel_index(np.argmax(np.where(far[None, :], db, -1e9)), db.shape)
    assert abs(v[j] - VEL) < 0.1, (v[j], VEL)  # one Doppler bin is ~0.03 m/s
    # target moves toward the radar: range at mid-CPI is RANGE - v * (t_mid - t_0)
    assert abs(rng[i] - (RANGE - VEL * hdr["t_s"])) < 0.5, (rng[i], hdr["t_s"])
    st = client.get("/api/file").json()
    assert st["frame"]["sweep_period_ms"] == pytest.approx(30.6, abs=0.5)  # measured from the burst times
    assert st["frame"]["valid_steps"] == [True, True]


def test_control_step_seek_and_params(client, runs):
    root, _ = runs
    client.post("/api/open", json={"path": str(root / "sim_run"), "m": 16})
    s = client.post("/api/control", json={"action": "step", "delta": 3}).json()
    assert s["pos"] == 18 and not s["playing"]
    s = client.post("/api/control", json={"action": "seek", "pos": 2}).json()
    assert s["pos"] == 15  # clamped: a full window is needed
    s = client.post("/api/control", json={"action": "set", "m": 8, "window": "rect"}).json()
    assert s["params"]["m"] == 8 and s["params"]["window"] == "rect"
    assert client.post("/api/control", json={"window": "bogus"}).status_code == 400


def test_report_shown_for_incomplete_run(runs, tmp_path):
    root, run = runs
    from b210_sar.dataset import write_run
    b = run.bursts[:-1]  # drop the last burst: last sweep incomplete
    write_run(root / "short_run", dataclasses.replace(run, bursts=b))
    with TestClient(create_app(data_roots=[root])) as c:
        c.post("/api/open", json={"path": str(root / "short_run"), "m": 16})
        meta = c.get("/api/file").json()["meta"]
    assert meta["report"]["ok"] is False and meta["report"]["incomplete_count"] == 1
    assert meta["report"]["complete_sweeps"] == 65


def test_paths_outside_roots_refused(client, runs, tmp_path):
    root, _ = runs
    outside = tmp_path / "outside_run"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    try:
        for p in (str(outside), str(root / ".." / outside.name), str(root / "sim_run" / ".." / ".." / "etc"),
                  str(root / "link"), "/etc/passwd", "../../etc"):
            r = client.post("/api/open", json={"path": p})
            assert r.status_code in (403, 404), (p, r.status_code)
            assert client.get("/api/files", params={"path": p}).status_code in (403, 404)
        assert client.post("/api/open", json={"path": str(outside)}).status_code == 403
        assert client.post("/api/open", json={"path": str(root / "link")}).status_code == 403
        names = [e["name"] for e in client.get("/api/files", params={"path": str(root)}).json()["entries"]]
        assert "link" not in names and "sim_run" in names
        assert client.get("/api/file").json()["state"]["open"] is False
    finally:
        os.unlink(root / "link")


def test_listing_roots_and_blas_pin(client, runs):
    root, _ = runs
    top = client.get("/api/files").json()
    assert [e["path"] for e in top["entries"]] == [str(root.resolve())]
    kinds = {e["name"]: e["kind"] for e in client.get("/api/files", params={"path": str(root)}).json()["entries"]}
    assert kinds["sim_run"] == "run"
    import b210_sar.gui  # noqa: F401
    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"
