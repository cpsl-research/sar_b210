"""Skeleton checks. Replace with per-task tests as each task is implemented."""

import dataclasses
import importlib
import pkgutil
import re
from pathlib import Path

import yaml

import b210_sar
from b210_sar.config import RunConfig
from b210_sar.dataset import BURST_COLUMNS

ROOT = Path(__file__).resolve().parents[1]


def test_all_modules_import():
    for module in pkgutil.walk_packages(b210_sar.__path__, "b210_sar."):
        importlib.import_module(module.name)


def test_default_config_matches_schema():
    raw = yaml.safe_load((ROOT / "configs/default.yaml").read_text())
    fields = {f.name: f.type for f in dataclasses.fields(RunConfig)}
    assert set(raw) == set(fields)
    for section, type_name in fields.items():
        schema = getattr(importlib.import_module("b210_sar.config"), type_name)
        assert set(raw[section]) == {f.name for f in dataclasses.fields(schema)}, section


def test_default_config_numbers_parse_as_numbers():
    waveform = yaml.safe_load((ROOT / "configs/default.yaml").read_text())["waveform"]
    assert all(isinstance(value, (int, float)) for value in waveform.values())


def test_burst_columns_match_cpp_writer():
    source = (ROOT / "capture/include/sar/burst_record.hpp").read_text()
    header = re.search(r'kBurstCsvHeader\s*=\s*"([^"]+)"', source).group(1)
    assert header.split(",") == list(BURST_COLUMNS)


def test_default_sweep_stays_inside_permitted_band():
    raw = yaml.safe_load((ROOT / "configs/default.yaml").read_text())
    waveform, center = raw["waveform"], raw["rf"]["center_freq_hz"]
    half_span = (waveform["num_steps"] - 1) * waveform["step_spacing_hz"] / 2
    half_span += waveform["chirp_bandwidth_hz"] / 2
    assert raw["permitted_band"]["min_hz"] <= center - half_span
    assert center + half_span <= raw["permitted_band"]["max_hz"]
