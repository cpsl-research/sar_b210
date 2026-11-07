"""Config library: the shipped configs (read-only) plus user copies in configs/user/ (gitignored).

Stdlib + PyYAML only. The shipped `configs/default.yaml` is never written; user configs are
save-as-new only (no overwrite), validated first, and written atomically.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from b210_sar.config import config_dir, parse_config, validate

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


def save_user(name: str, text: str, root: str | Path | None = None) -> Path:
    """Validate `text` and write it as a new user config. Refuses a shipped or existing name.

    Warnings do not block a save; any error does (ConfigLibraryError, `.issues` has the fields).
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
    if errors:
        raise ConfigLibraryError("config is invalid: " + "; ".join(str(i) for i in errors), errors)
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
