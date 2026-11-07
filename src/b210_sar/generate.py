"""Generate a run config from high-level targets (range resolution, max range, max velocity). Stdlib + PyYAML.

Closed form, same formulas as budget.py. Best effort like radar_gui's generator: an unreachable target never
raises; the result carries the achieved numbers and warnings, and `ok` is False only when `validate` finds errors.
The base config (default `default`) supplies everything not named here; nothing is written to disk.

Mapping, in order:
  1. B_tot = c/(2 range_res). B = base chirp bandwidth (capped by the analog-bandwidth limit and sample rate),
     df = base df/B ratio * B, N = 1 + ceil((B_tot-B)/df), then df = (B_tot-B)/(N-1) so B_tot is hit exactly.
  2. Tc = smallest whole-sample chirp with c*Tc/2 >= max_range (the limits table has no chirp-length row; the
     floor is the 2-sample rule of validate). dwell = chirps_per_dwell * Tc.
  3. v_amb = lambda/(4*N*step_time) >= max_velocity: retune and settle are measured constants, so only
     chirps_per_dwell is reduced (toward 1), and only if that reaches the target; otherwise the requested
     dwell is kept and a warning gives the achieved and best-possible v_amb.
  4. Warn if the band leaves permitted_band; run `validate`.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

from b210_sar.budget import SPEED_OF_LIGHT as C
from b210_sar.config import Issue, load_hw_limits, parse_config, validate


def _plain(v: float) -> str:
    """Plain decimal (PyYAML reads 56e6 as a string); 15 places so whole-sample chirp lengths stay whole."""
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
        return str(int(round(v)))
    return format(v, ".15f").rstrip("0").rstrip(".")


def set_value(text: str, section: str, key: str, value: float) -> str:
    """Replace one scalar in config YAML text, keeping every comment and other line."""
    lines, inside = text.split("\n"), False
    for i, ln in enumerate(lines):
        if re.match(r"^\S", ln):
            inside = ln.startswith(f"{section}:")
            continue
        m = inside and re.match(rf"^(\s+{re.escape(key)}:\s*)([^\s#]+)(.*)$", ln)
        if m:
            lines[i] = f"{m.group(1)}{_plain(value)}{m.group(3)}"
            return "\n".join(lines)
    raise ValueError(f"{section}.{key}: not found in the base config text")


def _num(name: str, v, positive: bool = True):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (positive and v <= 0):
        raise ValueError(f"{name}: expected a positive number")
    return float(v)


def _warn(field: str, msg: str) -> dict:
    return Issue(field, "warning", msg, kind="generator").to_dict()


def generate(range_res_m, max_range_m, max_velocity_mps=None, chirps_per_dwell=50, center_freq_hz=None,
             num_steps=None, base: str = "default", root: str | Path | None = None) -> dict:
    """Return {ok, yaml, achieved, targets, issues, timeline}; achieved keys: B_tot Hz, range_res m, R_amb m, v_amb m/s, plus the chosen waveform fields. ValueError only for malformed inputs."""
    from b210_sar import configs as lib
    from b210_sar.timeline import waveform_timeline

    rr, rmax = _num("range_res_m", range_res_m), _num("max_range_m", max_range_m)
    vmax = None if max_velocity_mps in (None, "") else _num("max_velocity_mps", max_velocity_mps)
    n_chirps = int(_num("chirps_per_dwell", chirps_per_dwell))
    fc_in = None if center_freq_hz in (None, "") else _num("center_freq_hz", center_freq_hz)
    n_in = None if num_steps in (None, "") else int(_num("num_steps", num_steps))
    path = lib._resolve(base, root)
    text = path.read_text()
    cfg, _ = parse_config(yaml.safe_load(text))
    if cfg is None:
        raise ValueError(f"base config {base!r} does not parse")
    w, t = cfg.waveform, cfg.timing
    fc = fc_in if fc_in is not None else cfg.rf.center_freq_hz
    warns: list[dict] = []

    # 1. bandwidth and steps
    b_tot_target = C / (2 * rr)
    bw_cap = load_hw_limits()["limits"]
    bw_cap = next(r for r in bw_cap if r["key"] == "rf.analog_bandwidth_hz")["max"]
    bw = min(w.chirp_bandwidth_hz, bw_cap, w.sample_rate_hz - 2 * abs(cfg.rf.lo_offset_hz))
    ratio = w.step_spacing_hz / w.chirp_bandwidth_hz if w.chirp_bandwidth_hz > 0 else 2 / 3
    df0 = ratio * bw
    if b_tot_target <= bw:
        n, bw, df = 1, b_tot_target, w.step_spacing_hz
        if n_in not in (None, 1):
            warns.append(_warn("waveform.num_steps", f"range resolution {rr:g} m needs only one step; num_steps={n_in} ignored"))
    else:
        n = n_in if n_in and n_in > 1 else 1 + math.ceil((b_tot_target - bw) / df0 - 1e-9)
        df = (b_tot_target - bw) / (n - 1)
        if df > bw:
            warns.append(_warn("waveform.step_spacing_hz", f"step spacing {df / 1e6:.2f} MHz exceeds the chirp bandwidth "
                               f"{bw / 1e6:.2f} MHz: adjacent chirps do not overlap, the stitched band has gaps"))

    # 2. chirp length and dwell
    n_c = max(2, math.ceil(2 * rmax / C * w.sample_rate_hz - 1e-9))
    tc = n_c / w.sample_rate_hz
    wait = max(t.settle_margin_s, t.command_lead_s + tc)

    def v_amb_for(k: int) -> float:
        return (C / fc) / (4 * n * (t.retune_s + wait + k * tc))

    # 3. velocity
    if vmax is not None and v_amb_for(n_chirps) < vmax:
        k = next((k for k in range(n_chirps - 1, 0, -1) if v_amb_for(k) >= vmax), None)
        if k is not None:
            warns.append(_warn("waveform.dwell_s", f"chirps_per_dwell reduced {n_chirps} -> {k} to reach max velocity {vmax:g} m/s"))
            n_chirps = k
        else:
            warns.append(_warn("waveform.dwell_s", f"velocity target unreachable: best v_amb = {v_amb_for(n_chirps):.2f} m/s "
                               f"for {vmax:g} m/s asked (retune {t.retune_s * 1e3:.1f} ms and settle {wait * 1e3:.1f} ms per step "
                               f"are measured constants; even 1 chirp per dwell only gives {v_amb_for(1):.2f} m/s)"))

    # 4. write fields
    out = text
    for sec, key, val in (("waveform", "num_steps", n), ("waveform", "step_spacing_hz", df), ("waveform", "chirp_bandwidth_hz", bw),
                          ("waveform", "chirp_length_s", tc), ("waveform", "dwell_s", n_chirps * tc)):
        out = set_value(out, sec, key, val)
    if fc_in is not None:
        out = set_value(out, "rf", "center_freq_hz", fc)
    out = (f"# Generated by b210_sar.generate from {path.stem}: range res {rr:g} m, max range {rmax:g} m"
           f"{'' if vmax is None else f', max velocity {vmax:g} m/s'}, {n_chirps} chirps/dwell. Inline tags below are the base's.\n") + out

    out_cfg, issues = parse_config(yaml.safe_load(out))
    timeline = None
    if out_cfg is not None:
        band = out_cfg.permitted_band
        half = ((n - 1) * df + bw) / 2
        if band.min_hz is not None and band.max_hz is not None and (fc - half < band.min_hz or fc + half > band.max_hz):
            warns.append(_warn("waveform", f"stitched band {(fc - half) / 1e6:.2f}-{(fc + half) / 1e6:.2f} MHz leaves permitted_band "
                               f"{band.min_hz / 1e6:.2f}-{band.max_hz / 1e6:.2f} MHz"))
        issues = validate(out_cfg).issues
        timeline = waveform_timeline(out_cfg)
    errors = [i for i in issues if i.severity == "error"]
    all_issues = [i.to_dict() for i in issues] + warns
    ach = None
    if timeline:
        tl = timeline
        ach = {"B_tot": tl["total_band_hz"], "range_res": tl["derived"]["range_resolution_m"],
               "R_amb": tl["derived"]["max_unambiguous_range_m"], "v_amb": tl["derived"]["max_unambiguous_velocity_mps"],
               "num_steps": n, "chirps_per_dwell": n_chirps, "chirp_bandwidth_hz": bw, "step_spacing_hz": df,
               "chirp_length_s": tc, "dwell_s": n_chirps * tc, "step_time_s": tl["timing"]["step_time_s"],
               "sweep_time_s": tl["timing"]["sweep_time_s"]}
    return {"ok": not errors, "yaml": out, "achieved": ach,
            "targets": {"range_res_m": rr, "max_range_m": rmax, "max_velocity_mps": vmax, "chirps_per_dwell": int(chirps_per_dwell)},
            "issues": all_issues, "timeline": timeline}
