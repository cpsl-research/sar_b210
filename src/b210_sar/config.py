"""Run config schema, loader and validator, mirroring configs/default.yaml.

Stdlib + PyYAML only (no numpy), so console can import it as it imports radar_gui.cfg.

`validate` reproduces `capture/src/run_config.cpp validate_config` (internal consistency) and adds
the B210 hardware limits from `configs/hw_limits.yaml` (memo: console docs/research/2026-11-06-b210-hw-limits.md).
There is no locked TX block: `permitted_band` and `max_tx_gain_db` are per-config settings and are
checked only for self-consistency and against the hardware table.
"""

from __future__ import annotations

import dataclasses
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class DeviceConfig:
    args: str
    master_clock_rate_hz: float
    rx_timestamp_offset_ticks: int


@dataclass(frozen=True)
class WaveformConfig:
    sample_rate_hz: float
    num_steps: int
    step_spacing_hz: float
    chirp_bandwidth_hz: float
    chirp_length_s: float
    dwell_s: float
    taper_fraction: float
    tx_amplitude: float


@dataclass(frozen=True)
class RfConfig:
    center_freq_hz: float
    lo_offset_hz: float
    tx_gain_db: float
    max_tx_gain_db: float
    rx_gain_db: float
    tx_antenna: str
    rx_antenna: str
    tx_mode: str


@dataclass(frozen=True)
class PermittedBandConfig:
    min_hz: float | None
    max_hz: float | None
    legal_basis: str | None
    max_avg_eirp_dbm: float | None


@dataclass(frozen=True)
class TimingConfig:
    retune_s: float
    settle_margin_s: float
    command_lead_s: float
    lock_timeout_s: float
    clock_sync_interval_s: float
    max_run_s: float
    sync_mode: str


@dataclass(frozen=True)
class AntennaConfig:
    type: str
    gain_dbi: float | None
    beamwidth_deg: float
    tx_rx_separation_m: float


@dataclass(frozen=True)
class PlatformConfig:
    planned_speed_mps: float


@dataclass(frozen=True)
class OutputConfig:
    dataset_root: str | None


@dataclass(frozen=True)
class RunConfig:
    device: DeviceConfig
    waveform: WaveformConfig
    rf: RfConfig
    permitted_band: PermittedBandConfig
    timing: TimingConfig
    antenna: AntennaConfig
    platform: PlatformConfig
    output: OutputConfig


# ---------------------------------------------------------------------------------------------
# Locations

def config_dir() -> Path:
    """Directory holding default.yaml and hw_limits.yaml: $B210_SAR_CONFIG_DIR, else <repo>/configs."""
    env = os.environ.get("B210_SAR_CONFIG_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2] / "configs"


def hw_limits_path() -> Path:
    return config_dir() / "hw_limits.yaml"


_LIMITS_CACHE: dict[Path, dict] = {}


def load_hw_limits(path: str | Path | None = None) -> dict:
    """The hardware-limits table (read-only data). Cached per path."""
    p = Path(path) if path else hw_limits_path()
    if p not in _LIMITS_CACHE:
        _LIMITS_CACHE[p] = yaml.safe_load(p.read_text())
    return _LIMITS_CACHE[p]


# ---------------------------------------------------------------------------------------------
# Loading

class ConfigError(ValueError):
    """Raised by load_config; `.issues` carries the field-level problems."""

    def __init__(self, issues: list["Issue"]):
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues))


_OPTIONAL = {("permitted_band", "min_hz"), ("permitted_band", "max_hz"), ("permitted_band", "legal_basis"),
             ("permitted_band", "max_avg_eirp_dbm"), ("antenna", "gain_dbi"), ("output", "dataset_root")}
_INT_FIELDS = {("waveform", "num_steps"), ("device", "rx_timestamp_offset_ticks")}
_STR_FIELDS = {("device", "args"), ("rf", "tx_antenna"), ("rf", "rx_antenna"), ("rf", "tx_mode"),
               ("timing", "sync_mode"), ("antenna", "type"), ("permitted_band", "legal_basis"),
               ("output", "dataset_root")}


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_config(raw: Any) -> tuple[RunConfig | None, list["Issue"]]:
    """Build a RunConfig from a parsed YAML mapping. Returns (config or None, type/shape issues)."""
    issues: list[Issue] = []
    if not isinstance(raw, Mapping):
        return None, [Issue("<root>", "error", "config must be a YAML mapping", kind="schema")]
    for extra in sorted(set(raw) - set(_SECTION_TYPES_BY_NAME)):
        issues.append(Issue(extra, "error", "unknown section", kind="schema"))
    built = {}
    for name, cls in _SECTION_TYPES_BY_NAME.items():
        sec = raw.get(name)
        if not isinstance(sec, Mapping):
            issues.append(Issue(name, "error", "missing section", kind="schema"))
            continue
        names = [f.name for f in dataclasses.fields(cls)]
        for extra in sorted(set(sec) - set(names)):
            issues.append(Issue(f"{name}.{extra}", "error", "unknown field", kind="schema"))
        vals = {}
        for fname in names:
            key = (name, fname)
            where = f"{name}.{fname}"
            if fname not in sec:
                issues.append(Issue(where, "error", "missing field", kind="schema"))
                continue
            v = sec[fname]
            if v is None:
                if key in _OPTIONAL:
                    vals[fname] = None
                else:
                    issues.append(Issue(where, "error", "must not be null", kind="schema"))
                continue
            if key in _STR_FIELDS:
                ok = isinstance(v, str)
                what = "a string"
            elif key in _INT_FIELDS:
                ok = _is_num(v) and float(v) == int(v)
                what = "a whole number"
            else:
                ok = _is_num(v)
                what = "a number"
            if not ok:
                hint = " (PyYAML reads 56e6 as a string; write 56000000)" if isinstance(v, str) else ""
                issues.append(Issue(where, "error", f"must be {what}, got {v!r}{hint}", kind="schema"))
                continue
            vals[fname] = int(v) if key in _INT_FIELDS else (v if key in _STR_FIELDS else float(v))
        built[name] = vals
    if issues:
        return None, issues
    return RunConfig(**{n: _SECTION_TYPES_BY_NAME[n](**built[n]) for n in _SECTION_TYPES_BY_NAME}), []


_SECTION_TYPES = {"DeviceConfig": DeviceConfig, "WaveformConfig": WaveformConfig, "RfConfig": RfConfig,
                  "PermittedBandConfig": PermittedBandConfig, "TimingConfig": TimingConfig,
                  "AntennaConfig": AntennaConfig, "PlatformConfig": PlatformConfig, "OutputConfig": OutputConfig}
_SECTION_TYPES_BY_NAME = {f.name: _SECTION_TYPES[f.type] for f in dataclasses.fields(RunConfig)}


def load_config(path: str | Path) -> RunConfig:
    """Load a run config file and check its shape (types, required fields). Raises ConfigError.

    Shape only: call `validate` for hardware limits and internal consistency.
    """
    cfg, issues = parse_config(yaml.safe_load(Path(path).read_text()))
    if cfg is None:
        raise ConfigError(issues)
    return cfg


# ---------------------------------------------------------------------------------------------
# Validation

@dataclass(frozen=True)
class Issue:
    field: str
    severity: str  # error | warning
    message: str
    limit_key: str | None = None  # hw_limits.yaml row, when a hardware limit
    kind: str = "library"  # schema | library | chip | uhd | usb

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)  # limit keys that need a live device

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"valid": self.valid, "errors": [i.to_dict() for i in self.errors],
                "warnings": [i.to_dict() for i in self.warnings], "not_checked_offline": list(self.not_checked)}


def _is_whole(v: float) -> bool:
    return abs(v - round(v)) < 1e-6


def _fmt(v: float, unit: str) -> str:
    if unit == "Hz":
        return f"{v / 1e6:.6g} MHz"
    if unit == "s":
        return f"{v * 1e3:.6g} ms"
    return f"{v:.6g} {unit}"


def _rows(limits: dict) -> dict[str, dict]:
    return {r["key"]: r for r in limits["limits"]}


def step_centres(cfg: RunConfig) -> list[float]:
    n = cfg.waveform.num_steps
    return [cfg.rf.center_freq_hz + (i - (n - 1) / 2) * cfg.waveform.step_spacing_hz for i in range(n)]


def validate(cfg: "RunConfig | Mapping | None", limits: dict | None = None) -> Report:
    """Field-level validation: shape, internal consistency (as validate_config in the C++), hardware limits.

    `cfg` may be a RunConfig or a parsed YAML mapping. Severity per hardware row comes from hw_limits.yaml
    (chip/uhd error, usb/library warning). Rows of kind `usb` need a live device and are listed in
    `Report.not_checked`, never evaluated here.
    """
    report = Report()
    if not isinstance(cfg, RunConfig):
        cfg, schema_issues = parse_config(cfg)
        if cfg is None:
            report.issues = schema_issues
            return report
    limits = limits or load_hw_limits()
    rows = _rows(limits)
    w, rf, t, d, band = cfg.waveform, cfg.rf, cfg.timing, cfg.device, cfg.permitted_band
    add = lambda f, m, sev="error", key=None, kind="library": report.issues.append(Issue(f, sev, m, key, kind))

    def hw(key: str, fieldname: str, value: float, what: str | None = None) -> None:
        r = rows[key]
        label = what or fieldname
        lo, hi, unit = r.get("min"), r.get("max"), r["unit"]
        if lo is not None and value < lo:
            add(fieldname, f"{label} {_fmt(value, unit)} is below the {key} minimum {_fmt(lo, unit)} ({r['kind']} limit)",
                r["severity"], key, r["kind"])
        if hi is not None and value > hi:
            add(fieldname, f"{label} {_fmt(value, unit)} is above the {key} maximum {_fmt(hi, unit)} ({r['kind']} limit)",
                r["severity"], key, r["kind"])

    # --- internal consistency (capture/src/run_config.cpp validate_config)
    if d.master_clock_rate_hz <= 0 or w.sample_rate_hz <= 0 or w.chirp_length_s <= 0 or w.dwell_s <= 0:
        add("device.master_clock_rate_hz", "rates and durations must be positive")
        return report
    if w.sample_rate_hz > d.master_clock_rate_hz or not _is_whole(d.master_clock_rate_hz / w.sample_rate_hz):
        add("waveform.sample_rate_hz", "master clock must be a whole multiple of the sample rate")
    if not _is_whole(w.chirp_length_s * w.sample_rate_hz) or w.chirp_length_s * w.sample_rate_hz < 2:
        add("waveform.chirp_length_s", "chirp length must be a whole number of samples")
    if not _is_whole(w.dwell_s / w.chirp_length_s) or w.dwell_s < w.chirp_length_s:
        add("waveform.dwell_s", "dwell must be a whole number of chirps, or leakage will not stay in a fixed range window")
    if w.num_steps < 1:
        add("waveform.num_steps", "num_steps must be at least 1")
    if w.step_spacing_hz <= 0:
        add("waveform.step_spacing_hz", "step spacing must be positive")
    if w.chirp_bandwidth_hz <= 0:
        add("waveform.chirp_bandwidth_hz", "chirp bandwidth must be positive")
    if abs(rf.lo_offset_hz) + w.chirp_bandwidth_hz / 2 > w.sample_rate_hz / 2:
        add("rf.lo_offset_hz", "chirp plus LO offset does not fit in the sample rate")
    if not 0 <= w.taper_fraction <= 0.5:
        add("waveform.taper_fraction", "taper_fraction must be between 0 and 0.5")
    if not 0 < w.tx_amplitude <= 1:
        add("waveform.tx_amplitude", "tx_amplitude must be in (0, 1]")
    if rf.tx_mode != "timed_burst":
        add("rf.tx_mode", "rf.tx_mode must be timed_burst; continuous TX would break the EIRP budget")
    if t.sync_mode not in ("host_clock", "pps"):
        add("timing.sync_mode", "timing.sync_mode must be host_clock or pps")
    if t.retune_s < 0 or t.settle_margin_s < 0:
        add("timing.retune_s" if t.retune_s < 0 else "timing.settle_margin_s", "timing values must not be negative")
    for name in ("command_lead_s", "lock_timeout_s", "clock_sync_interval_s", "max_run_s"):
        if getattr(t, name) <= 0:
            add(f"timing.{name}", "timing values must be positive")
    if None in (band.min_hz, band.max_hz, band.legal_basis, band.max_avg_eirp_dbm):
        add("permitted_band", "permitted_band is incomplete: capture will not run without a legal basis")
    elif w.num_steps >= 1:
        half = (w.num_steps - 1) * w.step_spacing_hz / 2 + w.chirp_bandwidth_hz / 2
        lo, hi = rf.center_freq_hz - half, rf.center_freq_hz + half
        if lo < band.min_hz or hi > band.max_hz:
            add("waveform", f"sweep occupies {lo / 1e6:.2f}-{hi / 1e6:.2f} MHz, outside permitted_band "
                f"{band.min_hz / 1e6:.2f}-{band.max_hz / 1e6:.2f} MHz")
    if cfg.antenna.gain_dbi is None:
        add("antenna.gain_dbi", "antenna.gain_dbi is needed for the EIRP check")
    if cfg.antenna.tx_rx_separation_m <= 0:
        add("antenna.tx_rx_separation_m", "antenna.tx_rx_separation_m must be positive")
    if rf.tx_gain_db > rf.max_tx_gain_db:
        add("rf.tx_gain_db", f"rf.tx_gain_db {rf.tx_gain_db:g} dB is above this config's rf.max_tx_gain_db {rf.max_tx_gain_db:g} dB")

    # --- B210 hardware limits (hw_limits.yaml)
    if w.num_steps >= 1:
        centres = step_centres(cfg)
        for key in ("rf.center_freq_hz.rx_tune", "rf.center_freq_hz.tx_tune"):
            for i in (0, len(centres) - 1) if len(centres) > 1 else (0,):
                before = len(report.issues)
                hw(key, "rf.center_freq_hz", centres[i], f"step {i} centre")
                if len(report.issues) > before:
                    break
    hw("rf.tx_gain_db", "rf.tx_gain_db", rf.tx_gain_db)
    hw("rf.tx_gain_db", "rf.max_tx_gain_db", rf.max_tx_gain_db)
    hw("rf.rx_gain_db", "rf.rx_gain_db", rf.rx_gain_db)
    hw("device.master_clock_rate_hz.range_1ch", "device.master_clock_rate_hz", d.master_clock_rate_hz)
    hw("device.master_clock_rate_hz.recommended_max", "device.master_clock_rate_hz", d.master_clock_rate_hz)
    hw("waveform.sample_rate_hz.range_1ch", "waveform.sample_rate_hz", w.sample_rate_hz)
    hw("rf.analog_bandwidth_hz", "waveform.chirp_bandwidth_hz", w.chirp_bandwidth_hz)
    bw = rows["rf.analog_bandwidth_hz"]
    if w.sample_rate_hz > bw["max"]:  # coupling note: capture asks bandwidth = sample rate and UHD clips it
        add("waveform.sample_rate_hz", f"sample rate {_fmt(w.sample_rate_hz, 'Hz')} exceeds the {_fmt(bw['max'], 'Hz')} analog "
            "bandwidth; the filter is clipped, usable bandwidth stays at the limit", "warning", "rf.analog_bandwidth_hz", "uhd")
    for key, fname, value in (("rf.tx_antenna", "rf.tx_antenna", rf.tx_antenna), ("rf.rx_antenna", "rf.rx_antenna", rf.rx_antenna)):
        r = rows[key]
        if value not in r["allowed"]:
            add(fname, f"{value!r} is not a valid port; allowed ({key}): {', '.join(r['allowed'])}", r["severity"], key, r["kind"])
    hw("timing.retune_s", "timing.retune_s", t.retune_s)
    report.not_checked = [r["key"] for r in limits["limits"] if r["kind"] == "usb"]

    if report.valid:  # planned-speed warning (needs a well-formed budget)
        from b210_sar.budget import compute_budget
        b = compute_budget(cfg)
        if cfg.platform.planned_speed_mps > b.max_speed_lambda_over_3_mps:
            add("platform.planned_speed_mps", f"planned speed {cfg.platform.planned_speed_mps:.2f} m/s is over the lambda/3 "
                f"limit of {b.max_speed_lambda_over_3_mps:.2f} m/s", "warning")
    return report
