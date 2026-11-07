"""Headless click-path (tools/gui_clickpath.py). Skipped unless Playwright and a Chromium are installed."""

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("playwright")

TOOL = Path(__file__).resolve().parents[1] / "tools" / "gui_clickpath.py"


def test_configs_click_path():
    spec = importlib.util.spec_from_file_location("gui_clickpath", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gui_clickpath"] = mod
    spec.loader.exec_module(mod)
    res = mod.run()
    if "skipped" in res:
        pytest.skip(res["skipped"])
    assert not res["failed"], res["failed"]
    assert not any(res["page_errors"].values()), res["page_errors"]
