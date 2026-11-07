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


# ---- b210-25: timeline, generate, save anyway

def test_check_returns_default_timeline(client):
    text = client.get("/api/configs/default").json()["yaml"]
    tl = client.post("/api/configs/check", json={"yaml": text}).json()["report"]["timeline"]
    assert tl["num_steps"] == 2 and tl["chirps_per_dwell"] == 50
    assert tl["timing"]["step_time_s"] == pytest.approx(0.0182) and tl["timing"]["sweep_time_s"] == pytest.approx(0.0364)
    assert tl["total_band_hz"] == pytest.approx(75e6) and tl["derived"]["range_resolution_m"] == pytest.approx(2.0, abs=0.01)
    assert [s["centre_hz"] for s in tl["steps"]] == pytest.approx([2441750000 - 15e6, 2441750000 + 15e6])
    assert tl["overlap_hz"] == pytest.approx(15e6) and tl["permitted_band"]["max_hz"] == 2483500000
    assert client.post("/api/configs/check", json={"yaml": "a: [1"}).json()["report"]["timeline"] is None


def test_generate_endpoint_and_save_rules(client, root):
    r = client.post("/api/configs/generate", json={"range_res_m": 2.0, "max_range_m": 3000}).json()
    assert r["ok"] and r["report"]["valid"] and r["timeline"]["num_steps"] == r["achieved"]["num_steps"]
    assert client.post("/api/configs", json={"name": "gen1", "yaml": r["yaml"]}).status_code == 201
    assert client.post("/api/configs/generate", json={"range_res_m": -1, "max_range_m": 3000}).status_code == 400
    bad = client.post("/api/configs/generate", json={"range_res_m": 2.0, "max_range_m": 3000, "center_freq_hz": 1e10}).json()
    assert bad["ok"] is False
    assert client.post("/api/configs", json={"name": "gen2", "yaml": bad["yaml"]}).status_code == 400
    assert not (root / "user" / "gen2.yaml").exists()


def test_save_anyway_marks_invalid_and_never_touches_shipped(client, root):
    before = sha(root / "default.yaml")
    text = client.get("/api/configs/default").json()["yaml"]
    r = client.post("/api/configs", json={"name": "forced", "yaml": text, "fields": {"rf.tx_gain_db": 90}, "allow_invalid": True})
    assert r.status_code == 201 and r.json()["report"]["valid"] is False
    f = root / "user" / "forced.yaml"
    assert f.read_text().startswith("# INVALID")
    ent = next(c for c in client.get("/api/configs").json()["configs"] if c["name"] == "forced")
    assert ent["valid"] is False and ent["errors"]
    # still new names only, shipped read-only, non-YAML never saved
    assert client.post("/api/configs", json={"name": "forced", "yaml": text, "allow_invalid": True}).status_code == 409
    assert client.post("/api/configs", json={"name": "default", "yaml": text, "allow_invalid": True}).status_code == 409
    assert client.post("/api/configs", json={"name": "junk", "yaml": "a: [1", "allow_invalid": True}).status_code == 400
    assert sha(root / "default.yaml") == before


def test_generated_draft_round_trips_through_form_values(client, root):
    """Verify case (2 m, 3000 m, 5 m/s): the form shows rounded values; saving with only edited fields passes."""
    g = client.post("/api/configs/generate", json={"range_res_m": 2.0, "max_range_m": 3000, "max_velocity_mps": 5}).json()
    assert g["ok"]
    # what the form shows, edited fields only (none): YAML is the source of truth
    assert client.post("/api/configs", json={"name": "gen_ok", "yaml": g["yaml"], "fields": {}}).status_code == 201
    # one unrelated edit still saves (chirp_length_s untouched)
    r = client.post("/api/configs", json={"name": "gen_ok2", "yaml": g["yaml"], "fields": {"rf.rx_gain_db": 25}})
    assert r.status_code == 201, r.text
    # a hand-typed chirp length that is not a whole number of samples is rejected (visible error), not snapped
    bad = {"waveform.chirp_length_s": 0.0000200178}
    chk = client.post("/api/configs/check", json={"yaml": g["yaml"], "fields": bad}).json()["report"]
    assert not chk["valid"] and any(e["field"] == "waveform.chirp_length_s" for e in chk["errors"])
    assert client.post("/api/configs", json={"name": "gen_bad", "yaml": g["yaml"], "fields": bad}).status_code == 400
    # a whole-sample value typed to full precision is accepted: 1121 samples at 56 MS/s
    ok = {"waveform.chirp_length_s": 1121 / 56e6, "waveform.dwell_s": 50 * 1121 / 56e6}
    assert client.post("/api/configs/check", json={"yaml": g["yaml"], "fields": ok}).json()["report"]["valid"]
