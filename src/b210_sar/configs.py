"""Config library: the shipped configs (read-only) plus user copies in configs/user/ (gitignored).

Stdlib + PyYAML only. The shipped `configs/default.yaml` is never written; user configs are
save-as-new only (no overwrite), validated first, and written atomically.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

from b210_sar.config import config_dir, load_config, parse_config, validate

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_NOT_CONFIGS = {"hw_limits"}


class ConfigLibraryError(ValueError):
    """Refused save/delete; the message says why."""

    def __init__(self, message: str, issues: list | None = None):
        super().__init__(message)
        self.issues = issues or []


def user_dir(root: str | Path | None = None) -> Path:
    return (Path(root) if root else config_dir()) / "user"


def _stem(name: str) -> str:
    return name[:-5] if name.endswith(".yaml") else name


def _check_name(name: str) -> str:
    stem = _stem(name)
    if not NAME_RE.match(stem) or stem.endswith("."):
        raise ConfigLibraryError(f"invalid config name {name!r}: use letters, digits, '_', '-', '.' (max 64)")
    return stem


def shipped_names(root: str | Path | None = None) -> set[str]:
    base = Path(root) if root else config_dir()
    return {p.stem for p in base.glob("*.yaml") if p.stem not in _NOT_CONFIGS}


def _entry(path: Path, kind: str) -> dict:
    try:
        raw = yaml.safe_load(path.read_text())
        cfg, issues = parse_config(raw)
        if cfg is not None:
            report = validate(cfg)
            issues = report.issues
    except (OSError, yaml.YAMLError) as exc:
        from b210_sar.config import Issue
        issues = [Issue("<file>", "error", f"cannot read: {exc}", kind="schema")]
    errors = [i for i in issues if i.severity == "error"]
    return {"name": path.stem, "path": str(path), "kind": kind, "valid": not errors,
            "errors": [str(i) for i in errors], "warnings": [str(i) for i in issues if i.severity == "warning"]}


def list_configs(root: str | Path | None = None) -> list[dict]:
    """Shipped then user configs: name, path, kind (shipped|user), valid, errors, warnings."""
    base = Path(root) if root else config_dir()
    out = [_entry(p, "shipped") for p in sorted(base.glob("*.yaml")) if p.stem not in _NOT_CONFIGS]
    udir = user_dir(root)
    if udir.is_dir():
        out += [_entry(p, "user") for p in sorted(udir.glob("*.yaml"))]
    return out


def save_user(name: str, text: str, root: str | Path | None = None, allow_invalid: bool = False) -> Path:
    """Validate `text` and write it as a new user config. Refuses a shipped or existing name.

    Warnings do not block a save; any error does (ConfigLibraryError, `.issues` has the fields) unless
    `allow_invalid` ("save anyway"): the file is then written with a `# INVALID` header listing the errors, and
    shows INVALID in the list; capture and launch still refuse to run it. Text that is not YAML is never saved.
    """
    stem = _check_name(name)
    if stem in shipped_names(root):
        raise ConfigLibraryError(f"{stem!r} is a shipped config and is read-only; save under a new name")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigLibraryError(f"not valid YAML: {exc}") from exc
    cfg, issues = parse_config(raw)
    if cfg is not None:
        issues = validate(cfg).issues
    errors = [i for i in issues if i.severity == "error"]
    if errors and not (allow_invalid and isinstance(raw, dict)):
        raise ConfigLibraryError("config is invalid: " + "; ".join(str(i) for i in errors), errors)
    if errors:
        text = ("# INVALID: saved anyway with errors; capture and launch refuse to run this config.\n"
                + "".join(f"#   error: {i}\n" for i in errors) + text)
    udir = user_dir(root)
    udir.mkdir(parents=True, exist_ok=True)
    final = udir / f"{stem}.yaml"
    if final.exists():
        raise ConfigLibraryError(f"user config {stem!r} already exists; delete it first or pick a new name")
    fd, tmp = tempfile.mkstemp(dir=udir, prefix=".tmp-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, final)  # atomic, fails if the name appeared meanwhile
        except FileExistsError:
            raise ConfigLibraryError(f"user config {stem!r} already exists; delete it first or pick a new name")
    finally:
        os.unlink(tmp)
    return final


def delete_user(name: str, root: str | Path | None = None) -> None:
    """Delete a user config. Shipped configs are never deletable."""
    stem = _check_name(name)
    if stem in shipped_names(root) and not (user_dir(root) / f"{stem}.yaml").exists():
        raise ConfigLibraryError(f"{stem!r} is a shipped config and is read-only")
    path = user_dir(root) / f"{stem}.yaml"
    if not path.is_file():
        raise ConfigLibraryError(f"no user config named {stem!r}")
    path.unlink()


# ---------------------------------------------------------------------------------------------
# CLI: python -m b210_sar.configs --list | --show NAME | --check PATH [--json]

def _resolve(arg: str, root: str | Path | None = None) -> Path:
    """A path, or the name of a shipped/user config."""
    p = Path(arg)
    if p.is_file():
        return p
    stem = _stem(arg)
    for cand in (user_dir(root) / f"{stem}.yaml", (Path(root) if root else config_dir()) / f"{stem}.yaml"):
        if cand.is_file() and stem not in _NOT_CONFIGS:
            return cand
    raise FileNotFoundError(f"no config file or name {arg!r}")


def check_report(path: str | Path, run_dir: str | Path | None = None, num_sweeps: int = 64) -> dict:
    """Machine-readable check of one config file: validity, issues, derived numbers, limits table path."""
    from b210_sar.budget import derived_numbers
    from b210_sar.config import Issue, hw_limits_path

    out: dict = {"path": str(path), "hw_limits_path": str(hw_limits_path())}
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        out.update(valid=False, errors=[Issue("<file>", "error", f"cannot read: {exc}", kind="schema").to_dict()],
                   warnings=[], not_checked_offline=[], derived=None)
        return out
    cfg, issues = parse_config(raw)
    if cfg is None:
        out.update(valid=False, errors=[i.to_dict() for i in issues if i.severity == "error"],
                   warnings=[i.to_dict() for i in issues if i.severity == "warning"],
                   not_checked_offline=[], derived=None)
        return out
    out.update(validate(cfg).to_dict())
    out["derived"] = derived_numbers(cfg, run_dir, num_sweeps)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="python -m b210_sar.configs", description="B210 SAR config library")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="list shipped and user configs with validity")
    g.add_argument("--show", metavar="NAME_OR_PATH", help="print a config's YAML")
    g.add_argument("--check", metavar="NAME_OR_PATH", help="validate a config and print derived numbers")
    ap.add_argument("--json", action="store_true", help="machine-readable output (--list, --check)")
    ap.add_argument("--run-dir", help="--check: add measured sweep timing from a recorded run directory")
    ap.add_argument("--sweeps", type=int, default=64, help="--check: sweeps per Doppler CPI (default 64)")
    args = ap.parse_args(argv)

    if args.list:
        entries = list_configs()
        if args.json:
            print(json.dumps({"configs": entries}, indent=2))
        else:
            for e in entries:
                print(f"{e['kind']:8} {e['name']:24} {'valid' if e['valid'] else 'INVALID'}  {e['path']}")
                for msg in e["errors"]:
                    print(f"           error: {msg}")
        return 0
    try:
        path = _resolve(args.show or args.check)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.show:
        sys.stdout.write(path.read_text())
        return 0
    try:
        rep = check_report(path, args.run_dir, args.sweeps)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        from b210_sar.budget import compute_budget, format_budget
        print(f"{rep['path']}: {'valid' if rep['valid'] else 'INVALID'}")
        for k in ("errors", "warnings"):
            for i in rep[k]:
                print(f"  {k[:-1]}: {i['field']}: {i['message']}")
        if rep.get("derived"):
            cfg = load_config(path)
            print(format_budget(cfg, compute_budget(cfg)), end="")
            dd = rep["derived"]["delay_doppler"]["planned"]
            print(f"  delay-Doppler (planned, M={dd['num_sweeps']}): range res {dd['range_resolution_m']:.2f} m, "
                  f"R_amb {dd['max_unambiguous_range_m']:.0f} m, v span +/-{dd['velocity_span_mps']:.3f} m/s, "
                  f"dv {dd['velocity_resolution_mps']*100:.2f} cm/s")
            if args.run_dir:
                m, dm = rep["derived"]["measured_run"], rep["derived"]["delay_doppler"]["measured"]
                print(f"  measured sweep period ({m['sweeps']} sweeps in {args.run_dir}): mean {m['sweep_period_s'] * 1e3:.2f} ms, "
                      f"max {m['sweep_period_max_s'] * 1e3:.2f} ms (planned {rep['derived']['budget']['sweep_time_s'] * 1e3:.2f} ms)")
                print(f"  delay-Doppler (measured, M={dm['num_sweeps']}): range res {dm['range_resolution_m']:.2f} m, "
                      f"R_amb {dm['max_unambiguous_range_m']:.0f} m, v span +/-{dm['velocity_span_mps']:.3f} m/s, "
                      f"dv {dm['velocity_resolution_mps']*100:.2f} cm/s")
    return 0 if rep["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
