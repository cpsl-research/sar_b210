"""Headless-browser click-path for the Configs tab (b210-28). Needs Playwright + a Chromium; the radio is never opened.

    python tools/gui_clickpath.py [--shots DIR]          # from the repo root; needs fastapi, uvicorn, pyyaml, playwright

Starts its own GUI server (`--source none`, a temp copy of the shipped configs as --config-root, a free localhost
port), drives default -> New config -> Edit parameters -> save as new; an out-of-band centre (error shown, Save
blocked, save-anyway works); Generate -> Load as draft -> tweak -> save. Runs at 1280x900 and 390x844 and prints a
JSON summary (assertions, saved-file checks, page_errors). Exit 0 only if everything passed, 2 if no browser.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIZES = {"1280x900": (1280, 900), "390x844": (390, 844)}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(cfg_root: Path, port: int) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "B210_SAR_CONFIG_DIR": str(cfg_root)}
    p = subprocess.Popen([sys.executable, "-m", "b210_sar.gui", "--host", "127.0.0.1", "--port", str(port),
                          "--source", "none", "--config-root", str(cfg_root)], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/configs/fields", timeout=1).read()
            return p
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    p.terminate()
    raise RuntimeError("GUI server did not start")


def drive(page, base: str, cfg_root: Path, tag: str, shots: Path | None, log: list) -> None:
    def ok(cond, what):
        log.append({"check": f"{tag}: {what}", "ok": bool(cond)})

    def shot(name):
        if shots:
            page.screenshot(path=str(shots / f"{name}-{tag}.png"), full_page=True)

    def setf(key, value):
        loc = page.locator(f'#cfg-fields [name="{key}"]')
        with page.expect_response(lambda r: r.url.endswith("/api/configs/check")):
            loc.select_option(value) if loc.evaluate("e => e.tagName") == "SELECT" else loc.fill(value)
        page.wait_for_timeout(120)

    def val(key):
        return page.locator(f'#cfg-fields [name="{key}"]').input_value()

    def derived():
        return page.locator("#cfg-derived").inner_text()

    def pick(name):
        page.locator(f'#cfg-list tbody tr[data-name="{name}"]').first.click()
        page.wait_for_function("n => document.getElementById('cfg-title').textContent === n", arg=name)

    def save_as(name, anyway=False):
        page.fill("#cfg-newname", name)
        if anyway:
            page.check("#cfg-anyway")
        page.click("#btn-save")
        page.wait_for_selector(f'#cfg-list tbody tr[data-name="{name}"][data-kind="user"]')
        page.wait_for_function("n => document.getElementById('cfg-title').textContent === n", arg=name)

    page.goto(f"{base}/#configs")
    page.wait_for_selector("#cfg-list tbody tr")
    pick("default")
    ok(page.locator("#btn-new").is_visible() and page.locator("#cfg-mode").is_hidden(), "default selected, form locked, New config offered")
    ok(page.locator('#cfg-fields [name="rf.center_freq_hz"]').is_disabled(), "fields locked before New config")
    page.click("#btn-new")
    ok(page.locator("#cfg-mode").is_visible() and page.locator("#gen-card").is_hidden(), "New config opens the mode switch, Edit parameters")
    ok(page.locator('#cfg-fields [name="rf.center_freq_hz"]').is_enabled(), "form unlocked")
    ok(page.locator('#cfg-fields [name="rf.max_tx_gain_db"]').is_disabled(), "safety cap read-only")
    ok(page.locator('#cfg-fields details').count() == 4, "four collapsible groups")
    ok(val("waveform.chirp_length_s") == "20" and "= 1120 samples" in page.locator("#hint-samples").inner_text(), "chirp length 20 us = 1120 samples")
    wave0, der0, band0 = page.locator("#cfg-wave").inner_html(), derived(), page.locator("#cfg-band").inner_text()
    shot("edit-initial")

    setf("rf.center_freq_hz", "2450")
    setf("waveform.num_steps", "3")
    setf("waveform.step_spacing_hz", "15")
    setf("waveform.chirp_bandwidth_hz", "25")
    setf("timing.retune_s", "10")
    setf("waveform.chirps_per_dwell", "40")
    ok(val("waveform.dwell_s") == "0.8", "chirps per dwell 40 -> dwell 0.8 ms")
    band1 = page.locator("#cfg-band").inner_text()
    ok("2422.5 / 2477.5" in band1 and band1 != band0, f"start/stop line follows the edit: {band1}")
    ok(page.locator("#cfg-wave").inner_html() != wave0 and derived() != der0, "diagram and derived table changed")
    ok("valid" in page.locator("#cfg-badges").inner_text() and "INVALID" not in page.locator("#cfg-badges").inner_text(), "still valid")
    ok(page.locator("#cfg-fields label[data-issue='err']").count() == 0, "no error markers")
    shot("edit-valid")
    save_as(f"cp_a_{tag}")
    ok(True, "saved and listed")
    text = (cfg_root / "user" / f"cp_a_{tag}.yaml").read_text()
    for needle in ("center_freq_hz: 2450000000", "num_steps: 3 ", "step_spacing_hz: 15000000", "chirp_bandwidth_hz: 25000000",
                   "retune_s: 0.01 ", "dwell_s: 0.0008 ", "# [fixed]", "# [measure]", "max_tx_gain_db: 40"):
        ok(needle in text, f"saved YAML has {needle!r}")

    # out of band: error shown, Save blocked, save anyway works
    pick("default")
    page.click("#btn-new")
    setf("rf.center_freq_hz", "2490")
    ok(page.locator("#cfg-issues .bad").count() > 0 and "INVALID" in page.locator("#cfg-badges").inner_text(), "out-of-band centre: error and INVALID badge")
    ok(page.locator("#btn-save").is_disabled(), "Save blocked with errors")
    ok(page.locator("#anyway-wrap").is_visible(), "save-anyway offered")
    shot("edit-error")
    save_as(f"cp_oob_{tag}", anyway=True)
    ok((cfg_root / "user" / f"cp_oob_{tag}.yaml").is_file(), "save anyway wrote the file")

    # a deliberately non-whole chirp length shows the whole-sample marker and error
    pick("default")
    page.click("#btn-new")
    setf("waveform.chirp_length_s", "20.0179")
    ok(page.locator("#cfg-fields label[data-key='waveform.chirp_length_s'][data-issue='err']").count() == 1, "non-whole chirp length marked on its field")
    ok("whole number of samples" in page.locator("#cfg-issues").inner_text(), "non-whole chirp length: whole-sample error shown")
    setf("waveform.chirp_length_s", "20.017857143")
    ok(page.locator("#cfg-fields label[data-issue='err']").count() == 0, "20.017857143 us accepted")
    page.click("#btn-cancel")

    # Generate -> Load as draft -> tweak -> save
    pick("default")
    page.click("#btn-new")
    page.click("#btn-mode-gen")
    ok(page.locator("#gen-card").is_visible() and page.locator('#cfg-fields [name="rf.rx_gain_db"]').is_disabled(), "Generate mode shows the panel, locks the form")
    shot("generate")
    page.fill("#gen-rr", "2"); page.fill("#gen-rmax", "3000"); page.fill("#gen-vmax", "5")
    page.click("#btn-gen")
    page.wait_for_selector("#btn-gen-load:visible")
    page.click("#btn-gen-load")
    ok(page.locator('#cfg-fields [name="rf.rx_gain_db"]').is_enabled() and page.locator("#gen-card").is_hidden(), "Load as draft drops into Edit parameters")
    setf("rf.rx_gain_db", "25")
    ok(page.locator("#cfg-fields label[data-issue='err']").count() == 0, "generated draft with one tweak has no error")
    save_as(f"cp_gen_{tag}")
    ok(yaml_loads((cfg_root / "user" / f"cp_gen_{tag}.yaml").read_text())["rf"]["rx_gain_db"] == 25, "generated + tweaked config saved")


def yaml_loads(t):
    import yaml
    return yaml.safe_load(t)


def run(shots: Path | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    tmp = Path(tempfile.mkdtemp(prefix="b210_clickpath_"))
    for n in ("default.yaml", "hw_limits.yaml"):
        shutil.copy(REPO / "configs" / n, tmp / n)
    before = {n: (REPO / "configs" / n).read_bytes() for n in ("default.yaml", "hw_limits.yaml")}
    port = free_port()
    srv = start_server(tmp, port)
    log: list = []
    page_errors: dict = {}
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                return {"skipped": f"no browser: {exc}"}
            for tag, (w, h) in SIZES.items():
                page = browser.new_page(viewport={"width": w, "height": h})
                errs = page_errors.setdefault(tag, [])
                page.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
                page.on("console", lambda m, errs=errs: m.type == "error" and errs.append(m.text))
                drive(page, f"http://127.0.0.1:{port}", tmp, tag, shots, log)
                page.close()
            browser.close()
    finally:
        srv.terminate()
        try:
            srv.wait(5)
        except subprocess.TimeoutExpired:
            srv.kill()  # this script's own child, by handle
            srv.wait(5)
    log.append({"check": "shipped configs byte-identical", "ok": all((REPO / "configs" / n).read_bytes() == b for n, b in before.items())})
    shutil.rmtree(tmp, ignore_errors=True)
    return {"checks": log, "failed": [c["check"] for c in log if not c["ok"]], "page_errors": page_errors}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shots", type=Path, help="write screenshots here")
    a = ap.parse_args()
    if a.shots:
        a.shots.mkdir(parents=True, exist_ok=True)
    try:
        res = run(a.shots)
    except ImportError as exc:
        print(json.dumps({"skipped": f"playwright not installed: {exc}"}))
        return 2
    print(json.dumps(res, indent=1))
    return 2 if "skipped" in res else int(bool(res["failed"] or any(res["page_errors"].values())))


if __name__ == "__main__":
    raise SystemExit(main())
