"""Configs tab API: list, view, check, save-as-new, delete. All logic is b210_sar.configs / config / budget.

The shipped configs are read-only: no endpoint here ever writes or removes one.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from b210_sar import configs as lib
from b210_sar.budget import derived_numbers
from b210_sar.config import config_dir, hw_limits_path, parse_config, validate

# The stepped-waveform fields the form edits: "section.key" -> int (True) or float (False)
EDITABLE: dict[str, bool] = {
    "waveform.num_steps": True,
    "waveform.step_spacing_hz": False,
    "waveform.chirp_bandwidth_hz": False,
    "waveform.chirp_length_s": False,
    "waveform.dwell_s": False,
    "waveform.taper_fraction": False,
    "waveform.sample_rate_hz": False,
    "rf.rx_gain_db": False,
    "rf.tx_gain_db": False,
}


def _plain(v: float) -> str:
    """Plain decimal: PyYAML reads 56e6 as a string, so never emit an exponent."""
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
        return str(int(round(v)))
    return format(v, ".12f").rstrip("0").rstrip(".")


def set_fields(text: str, fields: dict) -> str:
    """Replace the values of EDITABLE fields in config YAML text, keeping every comment and other line."""
    lines = text.split("\n")
    for dotted, raw in fields.items():
        if dotted not in EDITABLE:
            raise ValueError(f"field {dotted!r} is not editable here")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            raise ValueError(f"{dotted}: expected a finite number")
        if EDITABLE[dotted] and abs(raw - round(raw)) > 1e-9:
            raise ValueError(f"{dotted}: expected a whole number")
        sec, key = dotted.split(".")
        inside, done = False, False
        for i, ln in enumerate(lines):
            if re.match(r"^\S", ln):
                inside = ln.startswith(f"{sec}:")
                continue
            m = inside and re.match(rf"^(\s+{re.escape(key)}:\s*)([^\s#]+)(.*)$", ln)
            if m:
                lines[i] = f"{m.group(1)}{_plain(raw)}{m.group(3)}"
                done = True
                break
        if not done:
            raise ValueError(f"{dotted}: not found in the YAML text")
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
                "warnings": [], "not_checked_offline": [], "derived": None}
    cfg, issues = parse_config(raw)
    if cfg is None:
        return {"valid": False, "errors": [i.to_dict() for i in issues if i.severity == "error"],
                "warnings": [i.to_dict() for i in issues if i.severity == "warning"],
                "not_checked_offline": [], "derived": None}
    rep = validate(cfg).to_dict()
    rep["derived"] = _clean(derived_numbers(cfg))
    return rep


def _values(text: str) -> dict:
    try:
        raw = yaml.safe_load(text)
        return {k: raw[k.split(".")[0]][k.split(".")[1]] for k in EDITABLE}
    except Exception:  # noqa: BLE001 - invalid text: the form just stays empty
        return {}


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
                "values": _values(text), "report": _report_text(text)}

    @r.post("/check")
    def check(body: dict):
        text = body.get("yaml")
        if not isinstance(text, str):
            return err("body needs 'yaml' (string)")
        try:
            if body.get("fields"):
                text = set_fields(text, body["fields"])
        except ValueError as exc:
            return err(str(exc))
        return {"ok": True, "yaml": text, "values": _values(text), "report": _report_text(text)}

    @r.post("")
    def save(body: dict):
        name, text = body.get("name"), body.get("yaml")
        if not isinstance(name, str) or not isinstance(text, str):
            return err("body needs 'name' and 'yaml' (strings)")
        try:
            if body.get("fields"):
                text = set_fields(text, body["fields"])
            path = lib.save_user(name, text, root)
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
