"""docker/app/setup_env.sh and the compose wiring (no radio, no root; compose parts skip without docker)."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "docker" / "app" / "setup_env.sh"
COMPOSE = REPO / "docker" / "app" / "compose.yaml"
ENTRY = REPO / "docker" / "app" / "entrypoint.sh"
KEYS = {"HOST_UID", "HOST_GID", "USB_GID", "USB_DEV", "RUN_ROOT", "GUI_HOST", "GUI_PORT"}


def _fake_usb(tmp_path, with_b210=True):
    sysfs, nodes = tmp_path / "sysfs", tmp_path / "usb"
    for vid, bus, dev in (("1d6b", 1, 1), ("2500", 3, 7)):
        if vid == "2500" and not with_b210:
            continue
        d = sysfs / f"{bus}-{dev}"
        d.mkdir(parents=True)
        (d / "idVendor").write_text(vid + "\n")
        (d / "busnum").write_text(f"{bus}\n")
        (d / "devnum").write_text(f"{dev}\n")
        n = nodes / f"{bus:03d}" / f"{dev:03d}"
        n.parent.mkdir(parents=True, exist_ok=True)
        n.write_text("")
    return sysfs, nodes


def _run(sysfs, nodes, *args):
    env = {**os.environ, "SYSFS_USB_DIR": str(sysfs), "USB_NODE_DIR": str(nodes)}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)


def _parse(text):
    return dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln and not ln.startswith("#"))


def test_syntax():
    for f in (SCRIPT, ENTRY):
        assert subprocess.run(["bash", "-n", str(f)]).returncode == 0


def test_print_keys_and_gid(tmp_path):
    sysfs, nodes = _fake_usb(tmp_path)
    r = _run(sysfs, nodes, "--non-interactive", "--print")
    assert r.returncode == 0, r.stderr
    kv = _parse(r.stdout)
    assert set(kv) == KEYS
    assert kv["USB_GID"] == str((nodes / "003" / "007").stat().st_gid)
    assert kv["HOST_UID"] == str(os.getuid()) and kv["HOST_GID"] == str(os.getgid())
    assert kv["USB_DEV"] == str(nodes)
    assert kv["RUN_ROOT"] == str(REPO / "runs")
    assert kv["GUI_HOST"] == "127.0.0.1" and kv["GUI_PORT"] == "8010"
    assert "003/007" in r.stderr


def test_print_writes_no_file(tmp_path):
    sysfs, nodes = _fake_usb(tmp_path)
    env_file = SCRIPT.parent / ".env"
    existed = env_file.exists()
    r = _run(sysfs, nodes, "--non-interactive", "--print")
    assert r.returncode == 0 and env_file.exists() == existed


def test_no_radio_falls_back(tmp_path):
    sysfs, nodes = _fake_usb(tmp_path, with_b210=False)
    r = _run(sysfs, nodes, "--non-interactive", "--print")
    assert r.returncode == 0
    assert "no B210" in r.stderr and _parse(r.stdout)["USB_GID"].isdigit()


def test_unknown_option():
    r = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
    assert r.returncode == 2


def test_nothing_transmits_by_default():
    text = COMPOSE.read_text() + ENTRY.read_text()
    assert "TRANSMIT" not in text and "transmit:" not in text
    assert "privileged" not in re.sub(r"(?m)^\s*#.*$", "", COMPOSE.read_text())


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not installed")
def test_compose_config_uses_env(tmp_path):
    envf = tmp_path / "sample.env"
    envf.write_text("HOST_UID=1234\nHOST_GID=2345\nUSB_GID=77\nUSB_DEV=/dev/bus/usb\n"
                    f"RUN_ROOT={tmp_path}\nGUI_HOST=100.83.97.21\nGUI_PORT=8123\n")
    r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(envf), "--profile", "hw", "config"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "not a docker command" in r.stderr:
        pytest.skip("docker compose plugin missing")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert re.search(r"group_add:\s*\n\s*- ['\"]?77['\"]?\s*\n", out)
    assert "100.83.97.21" in out and "8123" in out
    assert "1234:2345" in out
    assert re.search(rf"source: {re.escape(str(tmp_path))}\s+target: /runs", out)
    assert "c 189:* rwm" in out and "memlock" in out and "privileged" not in out
    r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--env-file", str(envf), "--profile", "demo",
                        "config", "--format", "json"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    services = json.loads(r.stdout)["services"]
    assert set(services) == {"demo"} and "network_mode" not in services["demo"]
    assert services["demo"]["ports"][0]["host_ip"] == "127.0.0.1"
