"""Configs tab API: list, view, check, save-as-new, delete. All logic is b210_sar.configs / config / budget.

The shipped configs are read-only: no endpoint here ever writes or removes one.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from b210_sar import configs as lib
from b210_sar.budget import derived_numbers
from b210_sar import generate as gen
from b210_sar.config import config_dir, hw_limits_path, parse_config, validate
from b210_sar.timeline import waveform_timeline

# Operator-editable fields, the one table the form is built from (served by GET /api/configs/fields).
# `scale`: display = SI * scale (decimal text, so conversion is exact). Excluded on purpose: device.*,
# output.*, permitted_band.*, rf.tx_mode, legal_basis, identity/paths, and rf.max_tx_gain_db (read-only, below).
_MHz, _us, _ms = "0.000001", "1000000", "1000"
_F = [
    # key, label, group, kind, unit, scale, hint
    ("rf.center_freq_hz", "centre frequency", "RF", "float", "MHz", _MHz, "middle of the stitched band"),
    ("rf.lo_offset_hz", "LO offset", "RF", "float", "MHz", _MHz, "0 = tune the LO on the centre"),
    ("rf.tx_gain_db", "TX gain", "RF", "float", "dB", "1", "never above the safety cap or the EIRP limit"),
    ("rf.rx_gain_db", "RX gain", "RF", "float", "dB", "1", "fixed for every step and run; no AGC"),
    ("rf.tx_antenna", "TX antenna port", "RF", "enum", "", "1", "B210 channel A port"),
    ("rf.rx_antenna", "RX antenna port", "RF", "enum", "", "1", "B210 channel A port"),
    ("waveform.sample_rate_hz", "sample rate", "Waveform", "float", "MS/s", _MHz, "master clock must be a whole multiple"),
    ("waveform.num_steps", "number of steps", "Waveform", "int", "", "1", "stepped-frequency bands stitched per sweep"),
    ("waveform.step_spacing_hz", "step spacing", "Waveform", "float", "MHz", _MHz, "centre-to-centre distance of adjacent steps"),
    ("waveform.chirp_bandwidth_hz", "chirp bandwidth", "Waveform", "float", "MHz", _MHz, "occupied band of one step"),
    ("waveform.chirp_length_s", "chirp length", "Waveform", "float", "us", _us, "must be a whole number of samples"),
    ("waveform.dwell_s", "dwell", "Waveform", "float", "ms", _ms, "whole number of chirps"),
    ("waveform.chirps_per_dwell", "chirps per dwell", "Waveform", "int", "", "1", "= dwell / chirp length; editing it sets the dwell"),
    ("waveform.taper_fraction", "taper fraction", "Waveform", "float", "", "1", "raised-cosine edge, fraction of the chirp"),
    ("waveform.tx_amplitude", "TX amplitude", "Waveform", "float", "", "1", "digital scale, fraction of full scale"),
    ("timing.retune_s", "retune", "Timing", "float", "ms", _ms, "tune, lock and command overhead per step"),
    ("timing.settle_margin_s", "settle margin", "Timing", "float", "ms", _ms, "wait after LO lock"),
    ("timing.command_lead_s", "command lead", "Timing", "float", "ms", _ms, "how far ahead timed commands are issued"),
    ("timing.lock_timeout_s", "lock timeout", "Timing", "float", "ms", _ms, ""),
    ("timing.clock_sync_interval_s", "clock sync interval", "Timing", "float", "s", "1", "USRP time logged against host clocks"),
    ("timing.max_run_s", "max run length", "Timing", "float", "s", "1", "sizes the RAM buffer"),
    ("timing.sync_mode", "sync mode", "Timing", "enum", "", "1", "time base"),
    ("platform.planned_speed_mps", "planned speed", "Capture", "float", "m/s", "1", "platform speed during capture"),
    ("antenna.gain_dbi", "antenna gain", "Capture", "float", "dBi", "1", "used for the EIRP check"),
    ("antenna.beamwidth_deg", "antenna beamwidth", "Capture", "float", "deg", "1", "sets the along-track spacing rule"),
]
_OPTIONS = {"rf.tx_antenna": ["TX/RX", "RX2"], "rf.rx_antenna": ["TX/RX", "RX2"], "timing.sync_mode": ["host_clock", "pps"]}
FIELDS: list[dict] = [dict(key=k, label=l, group=g, kind=kd, unit=u, scale=sc, hint=h,
                           **({"options": _OPTIONS[k]} if k in _OPTIONS else {}))
                      for k, l, g, kd, u, sc, h in _F]
_BY_KEY = {f["key"]: f for f in FIELDS}
EDITABLE: dict[str, bool] = {f["key"]: f["kind"] == "int" for f in FIELDS}  # kept for callers: key -> is_int
READONLY = [{"key": "rf.max_tx_gain_db", "label": "TX gain safety cap", "group": "RF", "kind": "float", "unit": "dB",
             "scale": "1", "hint": "capture refuses a higher TX gain; raise it only by hand-editing the YAML"}]
_CPD = "waveform.chirps_per_dwell"  # virtual: not a YAML key; writes waveform.dwell_s
_REAL = [k for k in _BY_KEY if k != _CPD]
_PREC = 50


def _dec(v) -> Decimal:
    """Exact Decimal of a number (its shortest repr) or a decimal string; ValueError for anything else."""
    if isinstance(v, bool) or v is None:
        raise ValueError("expected a number")
    try:
        d = Decimal(repr(v) if isinstance(v, float) else str(v).strip())
    except InvalidOperation:
        raise ValueError(f"{v!r} is not a number") from None
    if not d.is_finite():
        raise ValueError("expected a finite number")
    return d


def _txt(d: Decimal) -> str:
    """Plain decimal text, no exponent, no trailing zeros (PyYAML reads 56e6 as a string)."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def to_si(key: str, shown) -> str:
    """Display text (e.g. '20.017857143' us) -> SI decimal text for the YAML, by decimal shift (never float multiply)."""
    f = _BY_KEY.get(key)
    if f is None:
        raise ValueError(f"field {key!r} is not editable here")
    if f["kind"] in ("enum", "text"):
        s = str(shown)
        if "options" in f and s not in f["options"]:
            raise ValueError(f"{key}: {s!r} is not one of {', '.join(f['options'])}")
        return s
    with localcontext() as c:
        c.prec = _PREC
        d = _dec(shown) / Decimal(f["scale"])
        if f["kind"] == "int" and d != d.to_integral_value():
            raise ValueError(f"{key}: expected a whole number")
        return _txt(d)


def from_si(key: str, value) -> str:
    """SI value from the YAML -> display text. Inverse of to_si: from_si(k, to_si(k, s)) is s, normalised."""
    f = _BY_KEY.get(key) or next((r for r in READONLY if r["key"] == key), None)
    if f is None:
        raise ValueError(f"unknown field {key!r}")
    if f["kind"] in ("enum", "text"):
        return str(value)
    with localcontext() as c:
        c.prec = _PREC
        return _txt(_dec(value) * Decimal(f["scale"]))


def _plain(v: float) -> str:
    """Plain decimal text of a float (kept for callers)."""
    return _txt(_dec(float(v)))


def _si_text(key: str, raw) -> str:
    """SI value sent by an API client: a number, or numeric text, or (enum) a string; returns YAML text."""
    f = _BY_KEY[key]
    if f["kind"] in ("enum", "text"):
        if not isinstance(raw, str):
            raise ValueError(f"{key}: expected text")
        return to_si(key, raw)
    if isinstance(raw, str) and not raw.strip():
        raise ValueError(f"{key}: expected a number")
    if not isinstance(raw, (int, float, str)) or isinstance(raw, bool):
        raise ValueError(f"{key}: expected a finite number")
    try:
        d = _dec(raw)
    except ValueError:
        raise ValueError(f"{key}: expected a finite number") from None
    if f["kind"] == "int" and abs(d - d.to_integral_value()) > Decimal("1e-9"):
        raise ValueError(f"{key}: expected a whole number")
    return _txt(d.to_integral_value() if f["kind"] == "int" else d)


def _find(lines: list[str], dotted: str):
    sec, key = dotted.split(".")
    inside = False
    for i, ln in enumerate(lines):
        if re.match(r"^\S", ln):
            inside = ln.startswith(f"{sec}:")
            continue
        m = inside and re.match(rf"^(\s+{re.escape(key)}:\s*)(\"[^\"]*\"|[^\s#]+)(.*)$", ln)
        if m:
            return i, m
    raise ValueError(f"{dotted}: not found in the YAML text")


def _put(lines: list[str], dotted: str, txt: str, quote: bool = False) -> None:
    i, m = _find(lines, dotted)
    lines[i] = f"{m.group(1)}{chr(34) + txt + chr(34) if quote else txt}{m.group(3)}"


def set_fields(text: str, fields: dict, display: dict | None = None) -> str:
    """Replace the values of table fields in config YAML text, keeping every comment and other line.

    `fields` carries SI values (number or numeric text); `display` carries form text in display units, converted
    by decimal shift. Only the keys given are written. chirps_per_dwell writes dwell_s = n * chirp_length_s exactly.
    """
    lines = text.split("\n")
    todo: dict[str, str] = {}
    for src, conv in ((fields or {}, _si_text), (display or {}, to_si)):
        for dotted, raw in src.items():
            if dotted not in _BY_KEY:
                raise ValueError(f"field {dotted!r} is not editable here")
            if dotted in todo:
                raise ValueError(f"{dotted}: given twice")
            todo[dotted] = conv(dotted, raw)
    n = todo.pop(_CPD, None)
    if n is not None and "waveform.dwell_s" in todo:
        raise ValueError("give either waveform.dwell_s or waveform.chirps_per_dwell, not both")
    for dotted, txt in todo.items():
        _put(lines, dotted, txt, quote=_BY_KEY[dotted]["kind"] in ("enum", "text"))
    if n is not None:
        if int(n) < 1:
            raise ValueError("waveform.chirps_per_dwell: must be at least 1")
        _, m = _find(lines, "waveform.chirp_length_s")
        with localcontext() as c:
            c.prec = _PREC
            _put(lines, "waveform.dwell_s", _txt(Decimal(int(n)) * _dec(m.group(2))))
    return "\n".join(lines)


def _clean(o):
    """NaN/inf -> None so the response is strict JSON (an unset EIRP cap gives NaN in the budget)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _report_text(text: str) -> dict:
    """Validity, issues and derived numbers for config YAML text (nothing is written)."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return {"valid": False, "errors": [{"field": "<yaml>", "severity": "error", "kind": "schema",
                                            "message": f"not valid YAML: {exc}"}],
                "warnings": [], "not_checked_offline": [], "derived": None, "timeline": None}
    cfg, issues = parse_config(raw)
    if cfg is None:
        return {"valid": False, "errors": [i.to_dict() for i in issues if i.severity == "error"],
                "warnings": [i.to_dict() for i in issues if i.severity == "warning"],
                "not_checked_offline": [], "derived": None, "timeline": None}
    rep = validate(cfg).to_dict()
    rep["derived"] = _clean(derived_numbers(cfg))
    rep["timeline"] = _clean(waveform_timeline(cfg))
    return rep


def _values(text: str) -> dict:
    """SI values of every table field (and the read-only cap); chirps_per_dwell is derived."""
    try:
        raw = yaml.safe_load(text)
        out = {k: raw[k.split(".")[0]][k.split(".")[1]] for k in _REAL + [r["key"] for r in READONLY]}
        w = raw["waveform"]
        r = Decimal(repr(float(w["dwell_s"]))) / Decimal(repr(float(w["chirp_length_s"])))
        out[_CPD] = int(r) if r == r.to_integral_value() else float(round(r, 6))
        return out
    except Exception:  # noqa: BLE001 - invalid text: the form just stays empty
        return {}


def _display(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        try:
            out[k] = from_si(k, v) if k != _CPD else _txt(_dec(v))
        except ValueError:
            pass
    return out


def _view(text: str) -> dict:
    v = _values(text)
    return {"values": v, "display": _display(v)}


def make_router(root: str | Path | None = None) -> APIRouter:
    r = APIRouter(prefix="/api/configs")

    def base() -> Path:
        return Path(root) if root else config_dir()

    def find(name: str, kind: str | None) -> tuple[Path, str]:
        stem = lib._check_name(name)
        ship, user = base() / f"{stem}.yaml", lib.user_dir(root) / f"{stem}.yaml"
        if kind == "user" or (kind is None and stem not in lib.shipped_names(root)):
            if user.is_file():
                return user, "user"
        elif stem in lib.shipped_names(root):
            return ship, "shipped"
        raise FileNotFoundError(f"no config named {stem!r}")

    def err(msg: str, code: int = 400, **extra) -> JSONResponse:
        return JSONResponse({"ok": False, "error": msg, **extra}, status_code=code)

    @r.get("")
    def list_all():
        out = []
        for e in lib.list_configs(root):
            rep = _report_text(Path(e["path"]).read_text()) if e["valid"] else None
            out.append({**e, "derived": rep["derived"] if rep else None})
        return {"configs": out, "hw_limits_path": str(hw_limits_path()), "editable": list(EDITABLE)}

    @r.get("/fields")
    def fields():
        return {"fields": FIELDS, "readonly": READONLY, "groups": ["RF", "Waveform", "Timing", "Capture"]}

    @r.get("/{name}")
    def view(name: str, kind: str | None = None):
        try:
            path, k = find(name, kind)
        except lib.ConfigLibraryError as exc:
            return err(str(exc))
        except FileNotFoundError as exc:
            return err(str(exc), 404)
        text = path.read_text()
        return {"name": path.stem, "kind": k, "read_only": k == "shipped", "yaml": text,
                **_view(text), "report": _report_text(text)}

    @r.post("/check")
    def check(body: dict):
        text = body.get("yaml")
        if not isinstance(text, str):
            return err("body needs 'yaml' (string)")
        try:
            if body.get("fields") or body.get("display"):
                text = set_fields(text, body.get("fields") or {}, body.get("display") or {})
        except ValueError as exc:
            return err(str(exc))
        return {"ok": True, "yaml": text, **_view(text), "report": _report_text(text)}

    @r.post("/generate")
    def generate(body: dict):
        try:
            res = gen.generate(body.get("range_res_m"), body.get("max_range_m"), body.get("max_velocity_mps"),
                               body.get("chirps_per_dwell") or 50, body.get("center_freq_hz"), body.get("num_steps"),
                               body.get("base") or "default", root)
        except (ValueError, FileNotFoundError) as exc:
            return err(str(exc))
        return {**_clean(res), **_view(res["yaml"]), "report": _report_text(res["yaml"])}

    @r.post("")
    def save(body: dict):
        name, text = body.get("name"), body.get("yaml")
        if not isinstance(name, str) or not isinstance(text, str):
            return err("body needs 'name' and 'yaml' (strings)")
        try:
            if body.get("fields") or body.get("display"):
                text = set_fields(text, body.get("fields") or {}, body.get("display") or {})
            path = lib.save_user(name, text, root, allow_invalid=body.get("allow_invalid") is True)
        except lib.ConfigLibraryError as exc:
            return err(str(exc), 409 if "exists" in str(exc) or "read-only" in str(exc) else 400,
                       issues=[i.to_dict() for i in exc.issues])
        except ValueError as exc:
            return err(str(exc))
        rep = _report_text(text)
        return JSONResponse({"ok": True, "name": path.stem, "kind": "user", "path": str(path),
                             "warnings": rep["warnings"], "report": rep}, status_code=201)

    @r.delete("/{name}")
    def delete(name: str, confirm: str = ""):
        try:
            stem = lib._check_name(name)
            if confirm != stem:
                return err(f"deleting needs confirm={stem}")
            lib.delete_user(stem, root)
        except lib.ConfigLibraryError as exc:
            return err(str(exc), 403 if "read-only" in str(exc) else 404 if "no user" in str(exc) else 400)
        return {"ok": True, "deleted": stem}

    return r
