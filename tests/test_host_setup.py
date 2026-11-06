"""Hardware-free tests for tools/setup/host_setup.py.

No root, no radio, no real sysfs: FakeHost serves files and dpkg answers from dicts and
records what --apply would execute.
"""
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools/setup"))
import host_setup as hs  # noqa: E402

USB = hs.USB_DEVICES


class FakeHost(hs.Host):
    def __init__(self, healthy=True, euid=1000, user="cpsl", group="cpsl"):
        self.files, self.dirs, self.pkgs, self.executed = {}, {}, set(), []
        self._euid, self._user, self._group = euid, user, group
        self.limits = (-1, 99)
        self.fail_on = None
        if healthy:
            self.pkgs.update(hs.PACKAGES)
            for f in hs.IMAGE_FILES:
                self.files[f"{hs.IMAGES_DIR}/{f}"] = ""
            self.files[hs.UHD_RULE] = ""
            self.files[hs.POWER_RULE] = hs.POWER_BODY + "\n"
            self.files[hs.LIMITS_FILE] = hs.limits_body(group) + "\n"
            self.files[hs.GOVERNOR] = "performance\n"
            self.files[hs.AUTOSUSPEND_PARAM] = "-1\n"
            self.attach("2-4", "on")

    def attach(self, name, control, speed="5000"):
        self.dirs.setdefault(USB, []).append(name)
        self.files[f"{USB}/{name}/idVendor"] = "2500\n"
        self.files[f"{USB}/{name}/power/control"] = control + "\n"
        self.files[f"{USB}/{name}/speed"] = speed + "\n"

    def read(self, path):
        return self.files.get(path)

    def listdir(self, path):
        return sorted(self.dirs.get(path, []))

    def exists(self, path):
        return path in self.files

    def run(self, argv):
        if argv[0] == "dpkg-query":
            return (0, "install ok installed") if argv[-1] in self.pkgs else (1, "")
        return 127, ""

    def euid(self):
        return self._euid

    def invoking_user(self):
        return self._user

    def primary_group(self, user):
        return self._group

    def session_limits(self):
        return self.limits

    def execute(self, step):
        self.executed.append(step.display())
        if self.fail_on and self.fail_on in step.display():
            return 1
        # model the effect so the re-check passes
        if step.kind == "write":
            self.files[step.path] = step.content + "\n"
        elif step.kind == "remove":
            self.files.pop(step.path, None)
        elif step.argv[:2] == ["apt-get", "install"]:
            self.pkgs.update(step.argv[4:])
        elif step.argv[0] == "uhd_images_downloader":
            for f in hs.IMAGE_FILES:
                self.files[f"{hs.IMAGES_DIR}/{f}"] = ""
        return 0


def by_name(checks):
    return {c.name: c for c in checks}


def test_healthy_host_all_ok_governor_warn():
    h = FakeHost()
    h.files[hs.GOVERNOR] = "powersave\n"
    cs = by_name(hs.run_checks(h))
    assert {n: c.status for n, c in cs.items() if c.status not in (hs.OK, hs.NA)} == {"governor": hs.WARN}
    assert hs.exit_code(list(cs.values())) == 0


def test_apt_missing_lists_only_missing_and_updates_first():
    h = FakeHost()
    h.pkgs -= {"uhd-host", "cmake"}
    c = hs.check_apt(h)
    assert c.status == hs.MISSING and "uhd-host cmake" in c.detail
    assert [s.argv[:2] for s in c.fix_cmds] == [["apt-get", "update"], ["apt-get", "install"]]
    assert c.fix_cmds[1].argv[-2:] == ["uhd-host", "cmake"] and "--no-install-recommends" in c.fix_cmds[1].argv


def test_images_missing_one_file():
    h = FakeHost()
    del h.files[f"{hs.IMAGES_DIR}/usrp_b200_fw.hex"]
    c = hs.check_images(h)
    assert c.status == hs.MISSING and "usrp_b200_fw.hex" in c.detail and "usrp_b210" not in c.detail
    assert c.fix_cmds[0].argv == ["uhd_images_downloader", "-t", "b2xx", "-i", hs.IMAGES_DIR]


def test_udev_uhd_missing_has_no_fix():
    h = FakeHost()
    del h.files[hs.UHD_RULE]
    c = hs.check_udev_host(h)
    assert c.status == hs.MISSING and not c.fix_cmds and c.manual


def test_udev_power_missing_and_different_write_exact_content_then_reload():
    for content in (None, "wrong\n"):
        h = FakeHost()
        if content is None:
            del h.files[hs.POWER_RULE]
        else:
            h.files[hs.POWER_RULE] = content
        c = hs.check_udev_power(h)
        assert c.status == hs.MISSING
        assert c.fix_cmds[0].kind == "write" and c.fix_cmds[0].content == hs.POWER_BODY
        assert c.fix_cmds[-2].argv == ["udevadm", "control", "--reload"]
        assert "--action=add" in c.fix_cmds[-1].argv


def test_stale_modprobe_removed_only_when_unchanged():
    h = FakeHost()
    h.files[hs.STALE_MODPROBE] = hs.STALE_BODY + "\n"
    c = hs.check_udev_power(h)
    assert c.status == hs.MISSING and c.fix_cmds[0].kind == "remove"
    h.files[hs.STALE_MODPROBE] = "options usbcore autosuspend=5\n"
    c = hs.check_udev_power(h)
    assert c.status == hs.OK and not c.fix_cmds and "left alone" in c.notes[0]


def test_limits_use_primary_group_and_body_matches_old_script():
    h = FakeHost(group="lab")
    assert hs.check_limits(h).status == hs.OK
    h.files[hs.LIMITS_FILE] = hs.limits_body("other") + "\n"
    c = hs.check_limits(h)
    assert c.status == hs.MISSING
    assert c.fix_cmds[0].content == (
        "# B210 / sar_b210: unlimited locked memory and realtime priority for @lab\n"
        "@lab - memlock unlimited\n@lab - rtprio 99")


def test_limits_bare_root_cannot_tell_group():
    h = FakeHost(euid=0, user=None)
    c = hs.check_limits(h)
    assert c.status == hs.WARN and not c.fix_cmds


def test_autosuspend_per_device():
    h = FakeHost()
    assert hs.check_autosuspend(h).status == hs.OK
    h.files[f"{USB}/2-4/power/control"] = "auto\n"
    c = hs.check_autosuspend(h)
    assert c.status == hs.MISSING and "2-4 power/control=auto" in c.detail


def test_autosuspend_no_device_uses_global_param_warn_only():
    h = FakeHost()
    h.dirs[USB] = []
    assert hs.check_autosuspend(h).status == hs.OK
    h.files[hs.AUTOSUSPEND_PARAM] = "2\n"
    c = hs.check_autosuspend(h)
    assert c.status == hs.WARN and hs.exit_code([c]) == 0


def test_governor_states():
    h = FakeHost()
    assert hs.check_governor(h).status == hs.OK
    h.files[hs.GOVERNOR] = "schedutil\n"
    assert hs.check_governor(h).status == hs.WARN
    del h.files[hs.GOVERNOR]
    assert hs.check_governor(h).status == hs.NA


@pytest.mark.parametrize("limits,status", [((-1, 99), hs.OK), ((65536, 99), hs.WARN),
                                           ((-1, 0), hs.WARN), ((2_000_000_000, 5), hs.OK)])
def test_session_limits_warn_only(limits, status):
    h = FakeHost()
    h.limits = limits
    c = hs.check_session(h)
    assert c.status == status


def test_usb_speed_and_ac():
    h = FakeHost()
    assert hs.check_usb(h).status == hs.OK
    h.files[f"{USB}/2-4/speed"] = "480\n"
    assert hs.check_usb(h).status == hs.WARN
    h.dirs[USB] = []
    assert hs.check_usb(h).status == hs.NA
    assert hs.check_ac(h).status == hs.NA
    h.dirs["/sys/class/power_supply"] = ["AC0", "BAT0"]
    h.files["/sys/class/power_supply/AC0/online"] = "0\n"
    assert hs.check_ac(h).status == hs.WARN


def test_scope_split():
    h = FakeHost()
    assert [c.name for c in hs.run_checks(h, "deps")] == ["apt", "images"]
    host = [c.name for c in hs.run_checks(h, "host")]
    assert "apt" not in host and "limits" in host and "autosuspend" in host
    assert all(c.scope == "deps" for c in hs.run_checks(h, "deps"))


def test_json_shape_consumable_like_radar():
    h = FakeHost()
    h.pkgs.discard("cmake")
    buf = io.StringIO()
    rc = hs.main(["--json"], host=h, out=buf)
    data = json.loads(buf.getvalue())
    assert rc == 1 and data["exit"] == 1 and data["would_run"] == []
    assert {"name", "status", "detail", "scope", "fix_cmds", "manual", "notes"} <= set(data["checks"][0])
    assert {c["name"]: c["status"] for c in data["checks"]}["apt"] == "MISSING"


def test_check_mode_quiet_contract(capsys):
    h = FakeHost()
    buf = io.StringIO()
    assert hs.main(["--check", "--scope", "deps"], host=h, out=buf) == 0
    assert buf.getvalue() == "" and capsys.readouterr().err == ""
    h.pkgs.discard("cmake")
    assert hs.main(["--check", "--scope", "deps"], host=h, out=buf) == 1
    assert buf.getvalue() == "" and capsys.readouterr().err.startswith("missing: apt:")


def test_report_mode_may_run_as_root_and_never_executes():
    h = FakeHost(euid=0)
    h.pkgs.clear()
    assert hs.main([], host=h, out=io.StringIO()) == 1
    assert h.executed == []


def test_apply_requires_root(capsys):
    h = FakeHost()
    h.pkgs.clear()
    assert hs.main(["--apply", "--yes"], host=h, out=io.StringIO()) == 2
    assert "needs root" in capsys.readouterr().err and h.executed == []


def test_apply_dry_run_prints_without_root_and_runs_nothing():
    h = FakeHost()
    h.pkgs.clear()
    buf = io.StringIO()
    assert hs.main(["--apply", "--dry-run", "--scope", "deps"], host=h, out=buf) == 1
    assert "would run" in buf.getvalue() and "apt-get install" in buf.getvalue() and h.executed == []


def test_apply_noop_on_satisfied_host():
    h = FakeHost(euid=0)
    buf = io.StringIO()
    assert hs.main(["--apply", "--yes"], host=h, out=buf) == 0
    assert h.executed == [] and "nothing to apply" in buf.getvalue()


def test_apply_fixes_prints_before_acting_and_rechecks():
    h = FakeHost(euid=0)
    h.pkgs.discard("cmake")
    del h.files[hs.POWER_RULE]
    del h.files[hs.LIMITS_FILE]
    buf = io.StringIO()
    assert hs.main(["--apply", "--yes"], host=h, out=buf) == 0
    text = buf.getvalue()
    assert text.index("\n# apt:") < text.index("apt-get install", text.index("\n# apt:")) and "re-check: [  OK   ] apt" in text
    assert [e.split()[0] for e in h.executed].count("apt-get") == 2
    assert h.files[hs.POWER_RULE] == hs.POWER_BODY + "\n"
    assert h.files[hs.LIMITS_FILE] == hs.limits_body("cpsl") + "\n"
    # idempotent: a second apply does nothing
    h.executed.clear()
    assert hs.main(["--apply", "--yes"], host=h, out=io.StringIO()) == 0 and h.executed == []


def test_apply_asks_and_skips_on_no():
    h = FakeHost(euid=0)
    h.pkgs.clear()
    asked = []
    rc = hs.main(["--apply", "--scope", "deps"], host=h, ask=lambda p: asked.append(p) or False, out=io.StringIO())
    assert rc == 1 and len(asked) == 1 and h.executed == []


def test_apply_stops_a_check_on_failed_command():
    h = FakeHost(euid=0)
    h.pkgs.clear()
    h.fail_on = "apt-get update"
    buf = io.StringIO()
    assert hs.main(["--apply", "--yes", "--scope", "deps"], host=h, out=buf) == 1
    assert "command failed" in buf.getvalue() and len(h.executed) == 1


def test_json_with_apply_needs_dry_run():
    with pytest.raises(SystemExit):
        hs.main(["--json", "--apply"], host=FakeHost(euid=0))
