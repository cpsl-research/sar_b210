"""Config library: load, validate (hardware limits + consistency), budget parity, user configs."""

import copy
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from b210_sar import budget as bud
from b210_sar import config as cfgmod
from b210_sar import configs as lib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "configs/default.yaml"
RUN = Path(os.environ.get("B210_TEST_RUN", "/home/rover/Documents/console/data/b210_runs/run_20261106_155516_rx"))
SAR_CAPTURE = next((p for p in (os.environ.get("SAR_CAPTURE"), ROOT / "build/sar_capture") if p and Path(p).exists()), None)


def raw():
    return copy.deepcopy(yaml.safe_load(DEFAULT.read_text()))


def check(r, **kw):
    return cfgmod.validate(r, **kw)


def fields(rep):
    return {i.field for i in rep.errors}


def test_shipped_default_loads_and_validates():
    c = cfgmod.load_config(DEFAULT)
    rep = cfgmod.validate(c)
    assert rep.valid and not rep.warnings
    assert set(rep.not_checked) == {"waveform.sample_rate_hz.usb3_streaming", "waveform.sample_rate_hz.usb2_streaming"}


def test_stdlib_and_yaml_only():
    code = "import sys; sys.modules['numpy']=None; sys.modules['scipy']=None; import b210_sar.config, b210_sar.budget, b210_sar.configs"
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})


# (mutation, field, limit key that must be named)
BAD = [
    (lambda r: (r["rf"].update(center_freq_hz=40e6), r["permitted_band"].update(min_hz=1)), "rf.center_freq_hz", "rx_tune"),
    (lambda r: (r["rf"].update(center_freq_hz=6100000000), r["permitted_band"].update(max_hz=7e9)), "rf.center_freq_hz", "tx_tune"),
    (lambda r: r["rf"].update(tx_gain_db=95, max_tx_gain_db=95), "rf.tx_gain_db", "89.8"),
    (lambda r: r["rf"].update(max_tx_gain_db=95), "rf.max_tx_gain_db", "89.8"),
    (lambda r: r["rf"].update(tx_gain_db=-1), "rf.tx_gain_db", "minimum"),
    (lambda r: r["rf"].update(rx_gain_db=80), "rf.rx_gain_db", "76"),
    (lambda r: r["device"].update(master_clock_rate_hz=62000000), "device.master_clock_rate_hz", "61.44"),
    (lambda r: r["device"].update(master_clock_rate_hz=1000000), "device.master_clock_rate_hz", "minimum"),
    (lambda r: r["waveform"].update(sample_rate_hz=62000000), "waveform.sample_rate_hz", "61.44"),
    (lambda r: r["waveform"].update(chirp_bandwidth_hz=57000000), "waveform.chirp_bandwidth_hz", "rf.analog_bandwidth_hz"),
    (lambda r: r["waveform"].update(chirp_bandwidth_hz=100000), "waveform.chirp_bandwidth_hz", "minimum"),
    (lambda r: r["rf"].update(tx_antenna="RX2"), "rf.tx_antenna", "TX/RX"),
    (lambda r: r["rf"].update(rx_antenna="TX2"), "rf.rx_antenna", "RX2"),
]


@pytest.mark.parametrize("mutate,fieldname,needle", BAD)
def test_hardware_limit_exceeded_rejected_naming_field_and_limit(mutate, fieldname, needle):
    r = raw()
    mutate(r)
    rep = check(r)
    hits = [i for i in rep.errors if i.field == fieldname and needle in (i.message + (i.limit_key or ""))]
    assert hits, [str(i) for i in rep.issues]
    assert all(i.kind in ("uhd", "chip") for i in hits if i.limit_key)


def test_warning_rows():
    r = raw()
    r["device"]["master_clock_rate_hz"] = 56000000
    r["timing"]["retune_s"] = 0.005
    rep = check(r)
    assert rep.valid
    assert [i.limit_key for i in rep.warnings] == ["timing.retune_s"]
    r = raw()
    r["device"]["master_clock_rate_hz"] = 61440000
    r["waveform"].update(sample_rate_hz=61440000, chirp_length_s=0.0000125)
    rep = check(r)
    keys = {i.limit_key: i.severity for i in rep.issues}
    assert keys["device.master_clock_rate_hz.recommended_max"] == "warning"
    assert keys["rf.analog_bandwidth_hz"] == "warning" and rep.valid


def test_different_band_and_cap_accepted_when_consistent():
    r = raw()
    r["rf"].update(center_freq_hz=5800000000, max_tx_gain_db=60, tx_gain_db=50)
    r["permitted_band"].update(min_hz=5725000000, max_hz=5875000000)
    rep = check(r)
    assert rep.valid, [str(i) for i in rep.issues]
    # a cap above the shipped one but under the hardware max is fine too
    r = raw()
    r["rf"].update(max_tx_gain_db=80)
    assert check(r).valid


def test_tx_gain_above_own_cap_rejected():
    r = raw()
    r["rf"]["tx_gain_db"] = 41
    assert "rf.tx_gain_db" in fields(check(r))
    r["rf"]["max_tx_gain_db"] = 50  # raising the config's own cap makes it consistent again
    assert check(r).valid


def test_sweep_outside_own_band_rejected():
    r = raw()
    r["permitted_band"]["max_hz"] = 2450000000
    rep = check(r)
    assert "waveform" in fields(rep) and "outside permitted_band" in rep.errors[0].message


def test_library_consistency_rules():
    for path, val, field in [(("waveform", "sample_rate_hz"), 50000000, "waveform.sample_rate_hz"),
                             (("waveform", "dwell_s"), 0.0010001, "waveform.dwell_s"),
                             (("rf", "tx_mode"), "continuous", "rf.tx_mode"),
                             (("timing", "sync_mode"), "gps", "timing.sync_mode"),
                             (("waveform", "tx_amplitude"), 1.5, "waveform.tx_amplitude"),
                             (("waveform", "num_steps"), 0, "waveform.num_steps"),
                             (("antenna", "gain_dbi"), None, "antenna.gain_dbi"),
                             (("permitted_band", "legal_basis"), None, "permitted_band")]:
        r = raw()
        r[path[0]][path[1]] = val
        assert field in fields(check(r)), path


def test_shape_errors_are_field_level():
    r = raw()
    r["waveform"]["sample_rate_hz"] = "56e6"
    del r["rf"]["tx_gain_db"]
    r["bogus"] = {}
    rep = check(r)
    assert {"waveform.sample_rate_hz", "rf.tx_gain_db", "bogus"} <= fields(rep)
    assert "56e6" in next(i for i in rep.errors if i.field == "waveform.sample_rate_hz").message


def test_planned_speed_warning():
    r = raw()
    r["platform"]["planned_speed_mps"] = 5
    rep = check(r)
    assert rep.valid and rep.warnings[0].field == "platform.planned_speed_mps"


# ---- budget: pinned from `sar_capture --check configs/default.yaml` (binary built from sar_b210 13efc74)
PINNED_CHECK = """Parameter budget
  occupied band        2404.25 - 2479.25 MHz (2 steps)
  range resolution     2.00 m
  sweep time           36.40 ms (27.47 sweeps/s)
  wavelength           12.28 cm
  max speed            1.12 m/s at lambda/3, 1.69 m/s for 60.00 deg beam
  planned speed        0.50 m/s
  position accuracy    15.35 mm relative
  data rate            12.31 MB/s
  RAM buffer           0.92 GB (4121 bursts)
  TX duty cycle        5.60 %
  TX conducted limit   -9.25 dBm average, 3.26 dBm during a burst if duty-cycle averaging applies
"""


def test_budget_matches_pinned_check_output():
    c = cfgmod.load_config(DEFAULT)
    assert bud.format_budget(c, bud.compute_budget(c)) == PINNED_CHECK


@pytest.mark.skipif(SAR_CAPTURE is None, reason="sar_capture not built (set SAR_CAPTURE or build capture/ into build/)")
def test_budget_parity_with_live_sar_capture_check():
    out = subprocess.run([str(SAR_CAPTURE), "--check", str(DEFAULT)], capture_output=True, text=True, check=True).stdout
    c = cfgmod.load_config(DEFAULT)
    assert bud.format_budget(c, bud.compute_budget(c)) == out


def test_planned_delay_doppler_default():
    c = cfgmod.load_config(DEFAULT)
    d = bud.delay_doppler(c)
    assert d.basis == "planned"
    assert d.sweep_period_s == pytest.approx(0.0364)
    assert d.total_bandwidth_hz == 75e6
    assert d.range_resolution_m == pytest.approx(2.0, abs=0.01)
    assert d.range_resolution_single_step_m == pytest.approx(3.33, abs=0.01)
    assert d.max_unambiguous_range_m == pytest.approx(2998, abs=1)
    assert d.velocity_span_mps == pytest.approx(0.84, abs=0.005)       # +/-0.84 m/s
    assert d.velocity_resolution_mps == pytest.approx(0.026, abs=0.0005)  # 2.6 cm/s at M=64


@pytest.mark.skipif(not RUN.exists(), reason="real run folder not on this machine")
def test_measured_basis_pinned_to_real_run():
    c = cfgmod.load_config(DEFAULT)
    m = bud.measured_sweep_period(RUN)
    assert m["sweeps"] == 980
    assert m["sweep_period_s"] == pytest.approx(0.03064, abs=2e-5)  # 30.64 ms (delay_doppler.md, b210-13)
    assert m["step_period_s"] == pytest.approx(0.01532, abs=2e-5)
    out = bud.derived_numbers(c, RUN)["delay_doppler"]
    assert out["planned"]["basis"] == "planned" and out["measured"]["basis"] == "measured"
    d = out["measured"]
    assert d["velocity_span_mps"] == pytest.approx(1.00, abs=0.005)
    assert d["velocity_resolution_mps"] == pytest.approx(0.031, abs=0.0005)
    assert d["doppler_resolution_hz"] == pytest.approx(0.51, abs=0.005)
    assert d["sweep_period_s"] < out["planned"]["sweep_period_s"]


# ---- user config library
@pytest.fixture
def lroot(tmp_path):
    shutil.copy(DEFAULT, tmp_path / "default.yaml")
    shutil.copy(ROOT / "configs/hw_limits.yaml", tmp_path / "hw_limits.yaml")
    return tmp_path


def test_list_save_delete(lroot):
    text = DEFAULT.read_text()
    names = [(e["name"], e["kind"], e["valid"]) for e in lib.list_configs(lroot)]
    assert names == [("default", "shipped", True)]  # hw_limits.yaml is data, not a config
    p = lib.save_user("mine", text.replace("tx_gain_db: 20 ", "tx_gain_db: 25 "), lroot)
    assert p == lroot / "user/mine.yaml"
    assert [e["name"] for e in lib.list_configs(lroot)] == ["default", "mine"]
    assert not list((lroot / "user").glob(".tmp-*"))
    lib.delete_user("mine", lroot)
    assert not p.exists()


def test_save_refusals(lroot):
    text = DEFAULT.read_text()
    before = (lroot / "default.yaml").read_text()
    with pytest.raises(lib.ConfigLibraryError, match="shipped"):
        lib.save_user("default", text, lroot)
    lib.save_user("a", text, lroot)
    with pytest.raises(lib.ConfigLibraryError, match="already exists"):
        lib.save_user("a.yaml", text, lroot)
    for bad in ("../x", "a/b", "", ".hidden", "x" * 80):
        with pytest.raises(lib.ConfigLibraryError, match="invalid config name"):
            lib.save_user(bad, text, lroot)
    with pytest.raises(lib.ConfigLibraryError, match="rx_gain_db") as e:
        lib.save_user("b", text.replace("rx_gain_db: 30", "rx_gain_db: 99"), lroot)
    assert e.value.issues and not (lroot / "user/b.yaml").exists()
    with pytest.raises(lib.ConfigLibraryError, match="not valid YAML"):
        lib.save_user("c", "a: [", lroot)
    with pytest.raises(lib.ConfigLibraryError, match="shipped"):
        lib.delete_user("default", lroot)
    with pytest.raises(lib.ConfigLibraryError, match="no user config"):
        lib.delete_user("nope", lroot)
    assert (lroot / "default.yaml").read_text() == before


def test_shipped_default_untouched_and_user_dir_ignored():
    assert subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", "configs/user/x.yaml"]).returncode == 0
    assert (ROOT / "configs/user/README.md").exists()


# ---- CLI (python -m b210_sar.configs) ----

def _cli(*args):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run([sys.executable, "-m", "b210_sar.configs", *args], capture_output=True, text=True, env=env)


def test_cli_check_json_default():
    import json
    p = _cli("--check", str(DEFAULT), "--json")
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout)
    assert d["valid"] is True and d["errors"] == []
    assert d["hw_limits_path"].endswith("hw_limits.yaml") and Path(d["hw_limits_path"]).is_file()
    assert abs(d["derived"]["delay_doppler"]["planned"]["range_resolution_m"] - 2.0) < 0.01
    assert "budget" in d["derived"]


def test_cli_check_json_invalid(tmp_path):
    import json
    r = raw()
    r["rf"]["tx_gain_db"] = 200
    r["rf"]["max_tx_gain_db"] = 200
    f = tmp_path / "bad.yaml"
    f.write_text(yaml.safe_dump(r))
    p = _cli("--check", str(f), "--json")
    assert p.returncode == 1
    d = json.loads(p.stdout)
    assert d["valid"] is False and any("tx_gain" in e["field"] for e in d["errors"])


def test_cli_list_show_and_missing():
    import json
    p = _cli("--list", "--json")
    assert p.returncode == 0
    names = {e["name"]: e for e in json.loads(p.stdout)["configs"]}
    assert names["default"]["valid"] is True and names["default"]["kind"] == "shipped"
    assert _cli("--show", "default").stdout == DEFAULT.read_text()
    assert _cli("--check", "nonexistent").returncode == 2
    assert "valid" in _cli("--check", "default").stdout


# ---------------------------------------------------------------------------------------------
# Python/C++ parity: tests/fixtures/config_parity.yaml is the shared table (capture_tests.cpp reads the same file)

PARITY = yaml.safe_load((ROOT / "tests/fixtures/config_parity.yaml").read_text())["cases"]


def _patched(case):
    r = raw()
    for dotted, value in case["set"].items():
        sec, key = dotted.split(".", 1)
        r[sec][key] = value
    return r


@pytest.mark.parametrize("case", PARITY, ids=[c["name"] for c in PARITY])
def test_parity_table_python(case):
    rep = cfgmod.validate(_patched(case))
    for got, want, kind in ((rep.errors, case.get("errors", []), "error"), (rep.warnings, case.get("warnings", []), "warning")):
        assert {i.field for i in got} == {w["field"] for w in want}, (kind, [str(i) for i in got])
        for w in want:
            assert any(i.field == w["field"] and w["text"] in i.message for i in got), (w, [str(i) for i in got])
    assert rep.valid == (not case.get("errors"))


def test_parity_table_cli_exit_codes(tmp_path):
    """The built binary's --check rejects exactly the table's invalid cases (the C++ unit test checks the wording)."""
    if SAR_CAPTURE is None:
        pytest.skip("sar_capture not built")
    if Path(SAR_CAPTURE).stat().st_mtime < (ROOT / "capture/src/run_config.cpp").stat().st_mtime:
        pytest.skip("sar_capture is older than capture/src/run_config.cpp (built before the hw_limits check)")
    for case in PARITY:
        p = tmp_path / f"{case['name']}.yaml"
        p.write_text(yaml.safe_dump(_patched(case), sort_keys=False))
        res = subprocess.run([str(SAR_CAPTURE), "--check", str(p)], capture_output=True, text=True,
                             env={**os.environ, "B210_SAR_CONFIG_DIR": str(ROOT / "configs")})
        assert (res.returncode == 0) == (not case.get("errors")), (case["name"], res.stderr)
        for w in case.get("errors", []):
            assert f"{w['field']}: " in res.stderr and w["text"] in res.stderr, (case["name"], res.stderr)
