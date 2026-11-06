"""T1: run config schema, mirroring configs/default.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


def load_config(path: str | Path) -> RunConfig:
    """Load and type-check a run config file."""
    raise NotImplementedError("T1")
