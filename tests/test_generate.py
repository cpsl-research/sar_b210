"""generate.py: targets -> config. Run: env -u PYTHONPATH uv run --extra gui pytest -q"""

import pytest
import yaml

from b210_sar.config import parse_config, validate
from b210_sar.generate import generate


def errors(res):
    return [i for i in res["issues"] if i["severity"] == "error"]


def test_range_target_default_base():
    r = generate(range_res_m=2.0, max_range_m=3000, chirps_per_dwell=50)
    assert r["ok"] and not errors(r)
    a = r["achieved"]
    assert a["B_tot"] == pytest.approx(74.9e6, rel=0.01)
    assert a["R_amb"] >= 3000 and a["num_steps"] == 2 and a["chirps_per_dwell"] == 50
    assert a["step_spacing_hz"] <= a["chirp_bandwidth_hz"]
    cfg, _ = parse_config(yaml.safe_load(r["yaml"]))
    assert validate(cfg).valid
    assert r["timeline"]["total_band_hz"] == pytest.approx(a["B_tot"])


def test_velocity_unreachable_is_best_effort():
    r = generate(2.0, 3000, max_velocity_mps=5)
    assert r["ok"] and r["achieved"]["v_amb"] == pytest.approx(0.84, abs=0.01)
    assert any("velocity target unreachable: best v_amb" in i["message"] for i in r["issues"] if i["severity"] == "warning")


def test_velocity_reachable_reduces_chirps_per_dwell():
    # default step: 9.2 ms retune + 8 ms settle + dwell, two steps; v_amb 0.89 needs <= 1 chirp, 0.86 needs fewer than 50
    r = generate(2.0, 3000, max_velocity_mps=0.86)
    assert r["achieved"]["chirps_per_dwell"] < 50 and r["achieved"]["v_amb"] >= 0.86


def test_band_outside_permitted_warns():
    r = generate(2.0, 3000, center_freq_hz=2.47e9)
    assert any(i["severity"] == "warning" and "permitted_band" in i["message"] for i in r["issues"])


def test_hw_error_row_gives_not_ok_without_raising():
    r = generate(2.0, 3000, center_freq_hz=1e10)  # above the 6 GHz LO limit
    assert r["ok"] is False and errors(r)


def test_coarse_range_uses_one_step_and_bad_inputs_raise():
    r = generate(10.0, 300)
    assert r["achieved"]["num_steps"] == 1 and r["achieved"]["B_tot"] == pytest.approx(14.99e6, rel=0.01)
    for bad in ({"range_res_m": 0, "max_range_m": 100}, {"range_res_m": 2, "max_range_m": "x"}):
        with pytest.raises(ValueError):
            generate(**bad)
