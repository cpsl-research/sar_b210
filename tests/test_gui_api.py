"""GUI API tests (httpx via Starlette TestClient). Run: env -u PYTHONPATH uv run --extra gui pytest -q"""

import hashlib
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from b210_sar.gui.app import create_app  # noqa: E402
from b210_sar.gui.configs_api import set_fields  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def root(tmp_path, monkeypatch):
    for n in ("default.yaml", "hw_limits.yaml"):
        shutil.copy(REPO / "configs" / n, tmp_path / n)
    monkeypatch.setenv("B210_SAR_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def client(root):
    with TestClient(create_app(config_root=root)) as c:
        yield c


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_health_state_index(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/state").json()["source"] == "none"
    assert "B210 SAR" in client.get("/").text
    assert client.get("/js/main.js").status_code == 200


def test_stream_sends_status(client):
    with client.websocket_connect("/stream") as ws:
        msg = ws.receive_json()
    assert msg["type"] == "status" and msg["source"] == "none"


def test_list_has_shipped_default_valid_with_derived(client):
    cfgs = client.get("/api/configs").json()["configs"]
    d = next(c for c in cfgs if c["name"] == "default")
    assert d["kind"] == "shipped" and d["valid"] is True
    assert d["derived"]["budget"]["total_bandwidth_hz"] > 0
    assert "planned" in d["derived"]["delay_doppler"]
    assert all(c["name"] != "hw_limits" for c in cfgs)


def test_view_shipped_read_only(client):
    j = client.get("/api/configs/default").json()
    assert j["read_only"] is True and "waveform:" in j["yaml"]
    assert j["values"]["waveform.num_steps"] == 2
    assert client.get("/api/configs/nope").status_code == 404
    assert client.get("/api/configs/..%2Fdefault").status_code in (400, 404)


def test_save_to_shipped_name_refused_and_file_unchanged(client, root):
    before = sha(root / "default.yaml")
    text = client.get("/api/configs/default").json()["yaml"]
    r = client.post("/api/configs", json={"name": "default", "yaml": text, "fields": {"rf.rx_gain_db": 31}})
    assert r.status_code == 409
    r = client.post("/api/configs", json={"name": "default.yaml", "yaml": text})
    assert r.status_code == 409
    assert sha(root / "default.yaml") == before
    assert client.delete("/api/configs/default?confirm=default").status_code in (403, 404)
    assert sha(root / "default.yaml") == before


def test_copy_edit_save_list_view_delete(client, root):
    text = client.get("/api/configs/default").json()["yaml"]
    f = {"waveform.num_steps": 2, "waveform.chirp_length_s": 0.00004, "rf.rx_gain_db": 25}
    chk = client.post("/api/configs/check", json={"yaml": text, "fields": f}).json()
    assert chk["ok"] and chk["values"]["waveform.chirp_length_s"] == 0.00004
    assert "# [fixed]" in chk["yaml"]  # comments preserved
    r = client.post("/api/configs", json={"name": "mine", "yaml": text, "fields": f})
    assert r.status_code == 201, r.text
    assert (root / "user" / "mine.yaml").is_file()
    cfgs = client.get("/api/configs").json()["configs"]
    mine = next(c for c in cfgs if c["name"] == "mine")
    assert mine["kind"] == "user"
    v = client.get("/api/configs/mine?kind=user").json()
    assert v["read_only"] is False and v["values"]["rf.rx_gain_db"] == 25
    assert client.post("/api/configs", json={"name": "mine", "yaml": text}).status_code == 409  # no overwrite
    # delete needs the confirmation
    assert client.delete("/api/configs/mine").status_code == 400
    assert (root / "user" / "mine.yaml").exists()
    assert client.delete("/api/configs/mine?confirm=mine").json()["deleted"] == "mine"
    assert not (root / "user" / "mine.yaml").exists()


def test_errors_block_save_warnings_do_not(client, root):
    text = client.get("/api/configs/default").json()["yaml"]
    bad = {"rf.tx_gain_db": 90}  # above the chip/UHD limit
    chk = client.post("/api/configs/check", json={"yaml": text, "fields": bad}).json()
    assert chk["report"]["valid"] is False and chk["report"]["errors"]
    r = client.post("/api/configs", json={"name": "bad", "yaml": text, "fields": bad})
    assert r.status_code == 400 and r.json()["issues"]
    assert not (root / "user" / "bad.yaml").exists()
    # a valid save reports its warnings (if any) without blocking
    ok = client.post("/api/configs", json={"name": "fine", "yaml": text})
    assert ok.status_code == 201 and isinstance(ok.json()["warnings"], list)


def test_bad_inputs(client):
    assert client.post("/api/configs/check", json={"yaml": "a: [1"}).json()["report"]["valid"] is False
    assert client.post("/api/configs", json={"name": "../x", "yaml": "a: 1"}).status_code == 400
    text = client.get("/api/configs/default").json()["yaml"]
    r = client.post("/api/configs/check", json={"yaml": text, "fields": {"rf.tx_antenna": 1}})
    assert r.status_code == 400
    r = client.post("/api/configs/check", json={"yaml": text, "fields": {"waveform.dwell_s": "x"}})
    assert r.status_code == 400


def test_set_fields_plain_decimals_and_scope():
    text = (REPO / "configs" / "default.yaml").read_text()
    out = set_fields(text, {"waveform.chirp_length_s": 0.00004, "waveform.step_spacing_hz": 3.2e7,
                            "rf.tx_gain_db": 10})
    assert "chirp_length_s: 0.00004" in out and "step_spacing_hz: 32000000" in out
    assert "e-" not in out.split("chirp_length_s:")[1].split("\n")[0].split("#")[0]
    assert "tx_gain_db: 10 " in out and "max_tx_gain_db: 40" in out  # max_ untouched
    assert out.count("\n") == text.count("\n")


def test_unknown_source_rejected():
    with pytest.raises(ValueError):
        create_app(source="bogus")
