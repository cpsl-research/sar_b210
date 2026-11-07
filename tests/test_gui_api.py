"""GUI API tests (httpx via Starlette TestClient). Run: env -u PYTHONPATH uv run --extra gui pytest -q"""

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

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


# ---- b210-28: every operator field editable, in display units --------------------------------------------

GROUPS = {"RF", "Waveform", "Timing", "Capture"}
EXPECTED = {
    "rf.center_freq_hz", "rf.lo_offset_hz", "rf.tx_gain_db", "rf.rx_gain_db", "rf.tx_antenna", "rf.rx_antenna",
    "waveform.sample_rate_hz", "waveform.num_steps", "waveform.step_spacing_hz", "waveform.chirp_bandwidth_hz",
    "waveform.chirp_length_s", "waveform.dwell_s", "waveform.chirps_per_dwell", "waveform.taper_fraction",
    "waveform.tx_amplitude", "timing.retune_s", "timing.settle_margin_s", "timing.command_lead_s",
    "timing.lock_timeout_s", "timing.clock_sync_interval_s", "timing.max_run_s", "timing.sync_mode",
    "platform.planned_speed_mps", "antenna.gain_dbi", "antenna.beamwidth_deg"}


def test_fields_endpoint_exact_list_and_groups(client):
    j = client.get("/api/configs/fields").json()
    keys = {f["key"] for f in j["fields"]}
    assert keys == EXPECTED
    assert {f["group"] for f in j["fields"]} == GROUPS and set(j["groups"]) == GROUPS
    assert not [k for k in keys if k.startswith(("device.", "permitted_band.", "output.")) or k == "rf.tx_mode"]
    assert "rf.max_tx_gain_db" not in keys and [r["key"] for r in j["readonly"]] == ["rf.max_tx_gain_db"]
    assert all({"key", "label", "group", "kind", "unit", "scale", "hint"} <= set(f) for f in j["fields"])
    ant = next(f for f in j["fields"] if f["key"] == "rf.tx_antenna")
    assert ant["kind"] == "enum" and ant["options"] == ["TX/RX", "RX2"]


def test_excluded_and_readonly_keys_rejected():
    text = (REPO / "configs" / "default.yaml").read_text()
    for k in ("device.args", "device.master_clock_rate_hz", "output.dataset_root", "permitted_band.min_hz",
              "permitted_band.legal_basis", "rf.tx_mode", "rf.max_tx_gain_db", "antenna.type", "nope.x"):
        for kw in ({"fields": {k: 1}}, {"display": {k: "1"}}):
            with pytest.raises(ValueError):
                set_fields(text, kw.get("fields", {}), kw.get("display", {}))


def test_antenna_and_enum_values_validated():
    text = (REPO / "configs" / "default.yaml").read_text()
    for k, bad in (("rf.tx_antenna", "TX2"), ("rf.rx_antenna", "rx2"), ("timing.sync_mode", "gps")):
        with pytest.raises(ValueError):
            set_fields(text, {k: bad})
    out = set_fields(text, {"rf.rx_antenna": "TX/RX", "timing.sync_mode": "pps"})
    assert 'rx_antenna: "TX/RX"' in out and 'sync_mode: "pps"' in out and "# [fixed] channel A" in out


def test_to_si_from_si_roundtrip_every_field():
    from b210_sar.gui.configs_api import FIELDS, from_si, to_si
    samples = {"float": ["1", "2450", "20.017857143", "0.5", "12.3456789"], "int": ["1", "3", "40"]}
    for f in FIELDS:
        if f["kind"] == "enum":
            for o in f["options"]:
                assert from_si(f["key"], to_si(f["key"], o)) == o
            continue
        for shown in samples[f["kind"]]:
            si = to_si(f["key"], shown)
            assert "e" not in si.lower()  # plain decimal text
            assert from_si(f["key"], float(si)) == shown, (f["key"], shown, si)
    assert to_si("rf.center_freq_hz", "2450") == "2450000000"
    assert to_si("waveform.chirp_length_s", "20.017857143") == "0.000020017857143"
    assert to_si("timing.retune_s", "10") == "0.01"
    with pytest.raises(ValueError):
        to_si("waveform.num_steps", "2.5")
    with pytest.raises(ValueError):
        to_si("rf.rx_gain_db", "abc")


GROUP_EDITS = {
    "RF": {"rf.center_freq_hz": "2445", "rf.lo_offset_hz": "0", "rf.tx_gain_db": "15", "rf.rx_gain_db": "25",
           "rf.rx_antenna": "TX/RX"},
    "Waveform": {"waveform.num_steps": "3", "waveform.step_spacing_hz": "15", "waveform.chirp_bandwidth_hz": "25",
                 "waveform.taper_fraction": "0.1", "waveform.tx_amplitude": "0.5", "waveform.chirps_per_dwell": "40"},
    "Timing": {"timing.retune_s": "10", "timing.settle_margin_s": "9", "timing.command_lead_s": "3",
               "timing.lock_timeout_s": "120", "timing.clock_sync_interval_s": "2", "timing.max_run_s": "90",
               "timing.sync_mode": "pps"},
    "Capture": {"platform.planned_speed_mps": "0.4", "antenna.gain_dbi": "9", "antenna.beamwidth_deg": "55"},
}


@pytest.mark.parametrize("group", sorted(GROUP_EDITS))
def test_each_group_edit_checks_and_saves_on_default(client, root, group):
    text = client.get("/api/configs/default").json()["yaml"]
    d = GROUP_EDITS[group]
    chk = client.post("/api/configs/check", json={"yaml": text, "display": d}).json()
    assert chk["ok"] and chk["report"]["valid"], chk["report"]["errors"]
    assert "# [fixed]" in chk["yaml"] and chk["yaml"].count("\n") == text.count("\n")  # comments/lines kept
    for k, shown in d.items():  # the form shows back what was typed
        assert chk["display"][k] == shown, k
    assert client.post("/api/configs", json={"name": f"g_{group}", "yaml": text, "display": d}).status_code == 201
    saved = yaml.safe_load((root / "user" / f"g_{group}.yaml").read_text())
    assert saved["rf"]["max_tx_gain_db"] == 40 and saved["device"]["args"].startswith("type=b200")


def test_display_units_write_si_and_chirps_per_dwell(client, root):
    text = client.get("/api/configs/default").json()["yaml"]
    d = {"rf.center_freq_hz": "2450", "waveform.num_steps": "3", "waveform.step_spacing_hz": "15",
         "waveform.chirp_bandwidth_hz": "25", "timing.retune_s": "10", "waveform.chirps_per_dwell": "40"}
    r = client.post("/api/configs", json={"name": "units", "yaml": text, "display": d})
    assert r.status_code == 201, r.text
    body = (root / "user" / "units.yaml").read_text()
    assert "center_freq_hz: 2450000000" in body and "num_steps: 3 " in body and "retune_s: 0.01 " in body
    assert "dwell_s: 0.0008 " in body  # 40 * 20 us
    v = client.get("/api/configs/units?kind=user").json()
    assert v["values"]["waveform.chirps_per_dwell"] == 40 and v["display"]["waveform.dwell_s"] == "0.8"
    assert v["display"]["rf.center_freq_hz"] == "2450" and v["values"]["rf.max_tx_gain_db"] == 40
    with pytest.raises(ValueError):
        set_fields(text, {"waveform.dwell_s": 0.001}, {"waveform.chirps_per_dwell": "10"})


def test_whole_sample_chirp_typed_in_us_and_non_whole_rejected(client, root):
    text = client.get("/api/configs/default").json()["yaml"]
    # 1121 samples at 56 MS/s = 20.017857142857143 us, typed to full precision (or rounded to 9 decimals:
    # 20.017857143 us, inside the 1e-6 sample tolerance): both accepted, dwell set by chirps per dwell
    ok = {"waveform.chirp_length_s": "20.017857142857143", "waveform.chirps_per_dwell": "50"}
    chk = client.post("/api/configs/check", json={"yaml": text, "display": ok}).json()
    assert chk["report"]["valid"], chk["report"]["errors"]
    assert client.post("/api/configs", json={"name": "ws", "yaml": text, "display": ok}).status_code == 201
    short = {"waveform.chirp_length_s": "20.017857143", "waveform.chirps_per_dwell": "50"}
    assert client.post("/api/configs", json={"name": "ws9", "yaml": text, "display": short}).status_code == 201
    # a hand-typed non-whole-sample value shows the whole-sample error and is never snapped
    bad = {"waveform.chirp_length_s": "20.0179", "waveform.chirps_per_dwell": "50"}
    chk = client.post("/api/configs/check", json={"yaml": text, "display": bad}).json()
    assert any(e["field"] == "waveform.chirp_length_s" and "whole number of samples" in e["message"]
               for e in chk["report"]["errors"])
    assert "chirp_length_s: 0.0000200179" in chk["yaml"]  # written as typed
    assert client.post("/api/configs", json={"name": "wsbad", "yaml": text, "display": bad}).status_code == 400


def test_generated_draft_then_edit_elsewhere_saves(client):
    g = client.post("/api/configs/generate", json={"range_res_m": 2.0, "max_range_m": 3000, "max_velocity_mps": 5}).json()
    assert g["ok"] and g["display"]["waveform.chirp_length_s"]
    r = client.post("/api/configs", json={"name": "gen_tweak", "yaml": g["yaml"], "display": {"rf.rx_gain_db": "25"}})
    assert r.status_code == 201, r.text
