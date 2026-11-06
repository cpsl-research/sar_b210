#!/usr/bin/env python3
"""Check (and optionally fix) the host prerequisites for sar_b210 capture with a USRP B210.

Default mode is a read-only *doctor*: every prerequisite is reported as OK, MISSING,
WARN or N-A, with the exact fix.  Exit status is 0 when nothing is MISSING, else 1.
Same shape and flags as CPSL_TI_Radar's tools/setup/host_setup.py, so console reads both.

    python3 tools/setup/host_setup.py                 # doctor (no root needed)
    python3 tools/setup/host_setup.py --json          # machine-readable
    python3 tools/setup/host_setup.py --check         # quiet: exit 0/1, "missing: ..." on stderr
    sudo python3 tools/setup/host_setup.py --apply    # print each fix, ask, run it (root)
    sudo python3 tools/setup/host_setup.py --apply --yes --scope deps   # container / install

Scopes (``--scope``, default ``all``):
  deps  apt packages (libuhd-dev uhd-host ...) and the B2xx FPGA/firmware images in
        /usr/share/uhd/images.  Needed in console's container too.
  host  udev (61-b210-power.rules), limits.d memlock/rtprio, USB autosuspend, plus
        report-only governor / AC / USB speed / current-session limits.  A container
        cannot set these (kernel and host files), so the container uses ``--scope deps``.

``--apply`` needs root (``sudo python3 ...``; no ``sudo`` inside, like scripts/deps/*.sh).
The limits file is written for the primary group of ``$SUDO_USER``.  Each fix is printed
before it runs, is idempotent (a satisfied check runs nothing), and is re-checked after.
``--dry-run`` prints and runs nothing.  Never opens the radio, never transmits.

Python stdlib only (runs from the system python3 as root, no venv).  The checks are pure
functions over a ``Host``; tests substitute a fake host.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field

OK, MISSING, WARN, NA = "OK", "MISSING", "WARN", "N-A"

PACKAGES = ("libuhd-dev", "uhd-host", "libyaml-cpp-dev", "libboost-system-dev",
            "cmake", "ninja-build")
IMAGES_DIR = "/usr/share/uhd/images"
IMAGE_FILES = ("usrp_b210_fpga.bin", "usrp_b200_fw.hex")
UHD_RULE = "/usr/lib/udev/rules.d/60-uhd-host.rules"          # shipped by uhd-host
POWER_RULE = "/etc/udev/rules.d/61-b210-power.rules"
POWER_BODY = (
    '# B210: no USB runtime autosuspend (power/control=on); 2500:0020 unloaded, 0021/0022 after firmware\n'
    'ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="2500", ATTR{idProduct}=="002[0-2]", '
    'TEST=="power/control", ATTR{power/control}="on"')
STALE_MODPROBE = "/etc/modprobe.d/usbcore-autosuspend.conf"   # written by an early script version
STALE_BODY = "options usbcore autosuspend=-1"
LIMITS_FILE = "/etc/security/limits.d/90-b210.conf"
AUTOSUSPEND_PARAM = "/sys/module/usbcore/parameters/autosuspend"
USB_DEVICES = "/sys/bus/usb/devices"
ETTUS_VID = "2500"
GOVERNOR = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
MEMLOCK_MIN = 1_900_000_000      # bytes; the RAM buffer of the default config + margin (check_host.sh: 1.9 GB)
ALL_SCOPES = ("deps", "host")


def limits_body(group: str) -> str:
    return (f"# B210 / sar_b210: unlimited locked memory and realtime priority for @{group}\n"
            f"@{group} - memlock unlimited\n@{group} - rtprio 99")


# ---------------------------------------------------------------- data types

@dataclass
class Step:
    """One fix action: run ``argv`` ("cmd"), write ``content`` to ``path`` ("write"),
    or delete ``path`` ("remove")."""

    kind: str
    argv: list = field(default_factory=list)
    path: str = ""
    content: str = ""

    def display(self) -> str:
        if self.kind == "write":
            return f"write {self.path}:\n" + "".join(f"    {ln}\n" for ln in self.content.splitlines()).rstrip("\n")
        if self.kind == "remove":
            return f"rm -f {self.path}"
        return shlex.join(self.argv)


def cmd(*argv) -> Step:
    return Step("cmd", argv=list(argv))


@dataclass
class Check:
    name: str
    status: str
    detail: str
    scope: str                                        # "deps" or "host"
    fix_cmds: list = field(default_factory=list)
    manual: list = field(default_factory=list)        # printed only, never run
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail,
                "scope": self.scope, "fix_cmds": [s.display() for s in self.fix_cmds],
                "manual": self.manual, "notes": self.notes}


class Host:
    """All I/O the checks need.  Read-only except ``execute`` (used by --apply)."""

    def read(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except (OSError, UnicodeDecodeError):
            return None

    def listdir(self, path: str) -> list:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def run(self, argv: list) -> tuple:
        """(returncode, stdout); 127 if the program is not installed."""
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        except FileNotFoundError:
            return 127, ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, exc.__class__.__name__
        return p.returncode, p.stdout

    def euid(self) -> int:
        return os.geteuid()

    def invoking_user(self) -> str | None:
        """$SUDO_USER under sudo; the current user otherwise; None for a bare root."""
        import pwd
        name = os.environ.get("SUDO_USER") if self.euid() == 0 else None
        name = name or pwd.getpwuid(os.getuid()).pw_name
        return None if name == "root" else name

    def primary_group(self, user: str) -> str | None:
        import grp
        import pwd
        try:
            return grp.getgrgid(pwd.getpwnam(user).pw_gid).gr_name
        except KeyError:
            return None

    def session_limits(self) -> tuple:
        """(memlock soft, rtprio soft) of this process; -1 = unlimited."""
        import resource
        return (resource.getrlimit(resource.RLIMIT_MEMLOCK)[0],
                resource.getrlimit(resource.RLIMIT_RTPRIO)[0])

    def execute(self, step: Step) -> int:
        """Run one fix step (no shell).  Only ever reached from --apply as root."""
        if step.kind == "write":
            with open(step.path, "w", encoding="utf-8") as f:
                f.write(step.content + "\n")
            return 0
        if step.kind == "remove":
            try:
                os.unlink(step.path)
            except FileNotFoundError:
                pass
            return 0
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        return subprocess.run(step.argv, env=env).returncode


# -------------------------------------------------------------------- checks

def _same(text: str | None, body: str) -> bool:
    return text is not None and text.rstrip("\n") == body


def check_apt(host: Host) -> Check:
    missing = []
    for pkg in PACKAGES:
        rc, out = host.run(["dpkg-query", "-W", "-f=${Status}", pkg])
        if rc != 0 or "install ok installed" not in out:
            missing.append(pkg)
    if not missing:
        return Check("apt", OK, "installed: " + " ".join(PACKAGES), "deps")
    return Check("apt", MISSING, "missing packages: " + " ".join(missing), "deps",
                 fix_cmds=[cmd("apt-get", "update"),
                           cmd("apt-get", "install", "-y", "--no-install-recommends", *missing)])


def check_images(host: Host) -> Check:
    missing = [f for f in IMAGE_FILES if not host.exists(f"{IMAGES_DIR}/{f}")]
    if not missing:
        return Check("images", OK, f"{', '.join(IMAGE_FILES)} in {IMAGES_DIR}", "deps")
    c = Check("images", MISSING, f"missing in {IMAGES_DIR}: " + " ".join(missing), "deps",
              fix_cmds=[cmd("uhd_images_downloader", "-t", "b2xx", "-i", IMAGES_DIR)])
    c.notes.append("needs network (files.ettus.com) and uhd-host (the apt check above)")
    return c


def check_udev_host(host: Host) -> Check:
    if host.exists(UHD_RULE):
        return Check("udev-uhd", OK, f"{UHD_RULE} present (uhd-host, MODE 0666)", "host")
    return Check("udev-uhd", MISSING, f"{UHD_RULE} missing (shipped by uhd-host)", "host",
                 manual=["install the deps first: --apply --scope deps"])


def check_udev_power(host: Host) -> Check:
    stale = host.read(STALE_MODPROBE)
    steps = []
    if _same(host.read(POWER_RULE), POWER_BODY):
        c = Check("udev-power", OK, f"{POWER_RULE} as expected", "host")
    else:
        steps.append(Step("write", path=POWER_RULE, content=POWER_BODY))
        c = Check("udev-power", MISSING, f"{POWER_RULE} missing or different", "host")
    if stale is not None:
        if _same(stale, STALE_BODY):
            steps.append(Step("remove", path=STALE_MODPROBE))
            if c.status == OK:
                c.status, c.detail = MISSING, f"stale {STALE_MODPROBE} (no effect with built-in usbcore)"
        else:
            c.notes.append(f"{STALE_MODPROBE} exists with other content; left alone")
    if steps:
        steps += [cmd("udevadm", "control", "--reload"),
                  cmd("udevadm", "trigger", "--subsystem-match=usb", "--action=add")]
        c.fix_cmds = steps
    return c


def check_limits(host: Host) -> Check:
    user = host.invoking_user()
    group = host.primary_group(user) if user else None
    if not group:
        return Check("limits", WARN, "cannot tell which group gets the limits (run via sudo from your "
                     "own user, not as root)", "host")
    body = limits_body(group)
    if _same(host.read(LIMITS_FILE), body):
        return Check("limits", OK, f"{LIMITS_FILE}: memlock unlimited, rtprio 99 for @{group}", "host")
    c = Check("limits", MISSING, f"{LIMITS_FILE} missing or different (want @{group})", "host",
              fix_cmds=[Step("write", path=LIMITS_FILE, content=body)])
    c.notes.append("applies at next login")
    return c


def b210_devices(host: Host) -> list:
    """[(sysfs name, power/control, speed Mb/s)] for attached Ettus (2500) USB devices."""
    out = []
    for d in host.listdir(USB_DEVICES):
        base = f"{USB_DEVICES}/{d}"
        if (host.read(f"{base}/idVendor") or "").strip() == ETTUS_VID:
            out.append((d, (host.read(f"{base}/power/control") or "").strip(),
                        (host.read(f"{base}/speed") or "").strip()))
    return out


def check_autosuspend(host: Host) -> Check:
    devs = b210_devices(host)
    fix = [Step("write", path=AUTOSUSPEND_PARAM, content="-1"),
           cmd("udevadm", "trigger", "--subsystem-match=usb", "--action=add")]
    if devs:
        bad = [f"{n} power/control={pc or '?'}" for n, pc, _ in devs if pc != "on"]
        if not bad:
            return Check("autosuspend", OK, "power/control=on on " + " ".join(n for n, _, _ in devs), "host")
        c = Check("autosuspend", MISSING, "want power/control=on: " + "; ".join(bad), "host", fix_cmds=fix)
        c.notes.append("the udev-power rule sets it persistently; replug the B210 if it stays 'auto'")
        return c
    val = (host.read(AUTOSUSPEND_PARAM) or "").strip()
    if val == "-1":
        return Check("autosuspend", OK, "no B210 attached; usbcore autosuspend = -1", "host")
    return Check("autosuspend", WARN, f"no B210 attached; usbcore autosuspend = {val or '?'} "
                 "(the udev-power rule covers the device once attached)", "host", fix_cmds=fix)


def check_governor(host: Host) -> Check:
    gov = (host.read(GOVERNOR) or "").strip()
    if not gov:
        return Check("governor", NA, "no cpufreq governor exposed", "host")
    if gov == "performance":
        return Check("governor", OK, "performance", "host")
    return Check("governor", WARN, f"{gov} (want performance; warn only)", "host",
                 manual=["sudo cpupower frequency-set -g performance"])


def check_session(host: Host) -> Check:
    mem, rt = host.session_limits()
    bad = []
    if mem != -1 and mem < MEMLOCK_MIN:
        bad.append(f"memlock {mem // 1024} KB (want unlimited or > 1.9 GB)")
    if rt <= 0:
        bad.append(f"rtprio {rt} (want > 0)")
    if not bad:
        return Check("session", OK, "memlock " + ("unlimited" if mem == -1 else f"{mem // 1024} KB")
                     + f", rtprio {rt}", "host")
    return Check("session", WARN, "; ".join(bad) + " in this session", "host",
                 notes=["limits.d applies at login: log out and back in after the limits fix"])


def check_usb(host: Host) -> Check:
    devs = b210_devices(host)
    if not devs:
        return Check("usb", NA, "no Ettus USB device attached", "host")
    slow = [f"{n} {s} Mb/s" for n, _, s in devs if s and s.isdigit() and int(s) < 5000]
    if slow:
        return Check("usb", WARN, "not SuperSpeed: " + "; ".join(slow) + " (use a USB 3 port, no hub)", "host")
    return Check("usb", OK, "attached: " + " ".join(f"{n} {s} Mb/s" for n, _, s in devs), "host")


def check_ac(host: Host) -> Check:
    for d in host.listdir("/sys/class/power_supply"):
        if d.startswith(("AC", "ADP")):
            online = (host.read(f"/sys/class/power_supply/{d}/online") or "").strip()
            if online:
                return (Check("ac", OK, "on AC power", "host") if online == "1"
                        else Check("ac", WARN, "on battery (run captures on AC)", "host"))
    return Check("ac", NA, "no AC adapter reported", "host")


CHECKS = (check_apt, check_images, check_udev_host, check_udev_power, check_limits,
          check_autosuspend, check_governor, check_session, check_usb, check_ac)
NAMES = ("apt", "images", "udev-uhd", "udev-power", "limits", "autosuspend",
         "governor", "session", "usb", "ac")


def run_checks(host: Host, scope: str = "all", only: str | None = None) -> list:
    out = []
    for fn, name in zip(CHECKS, NAMES):
        if only and name != only:
            continue
        c = fn(host)
        if scope == "all" or c.scope == scope:
            out.append(c)
    return out


def exit_code(checks: list) -> int:
    return 1 if any(c.status == MISSING for c in checks) else 0


def missing_list(checks: list) -> list:
    return [f"{c.name}: {c.detail}" for c in checks if c.status == MISSING]


# ---------------------------------------------------------------------- CLI

def format_check(c: Check) -> str:
    lines = [f"[{c.status:^7}] {c.name:<12} {c.detail}"]
    for s in c.fix_cmds:
        lines += [("           fix: " if i == 0 else "                ") + ln
                  for i, ln in enumerate(s.display().splitlines())]
    lines += ["           run by hand: " + m for m in c.manual]
    lines += ["           note: " + n for n in c.notes]
    return "\n".join(lines)


def _ask_tty(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scope", choices=("all",) + ALL_SCOPES, default="all",
                    help="deps = apt + images (also in the container); host = udev, limits, "
                         "autosuspend and report-only items (default all)")
    ap.add_argument("--only", choices=NAMES, help="run a single check")
    ap.add_argument("--apply", action="store_true",
                    help="run the fixes (root; each is printed first, one confirmation per check)")
    ap.add_argument("--yes", action="store_true", help="with --apply: do not ask")
    ap.add_argument("--dry-run", action="store_true", help="print the fixes, run nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check", action="store_true",
                    help="quiet: no stdout; exit 0/1, 'missing: ...' on stderr (what scripts/deps/*.sh --check does)")
    return ap


def main(argv=None, host: Host | None = None, ask=_ask_tty, out=None) -> int:
    host = host or Host()
    out = out or sys.stdout
    ap = build_parser()
    args = ap.parse_args(argv)
    executing = args.apply and not args.dry_run
    if args.json and executing:
        ap.error("--json reports only; combine it with --apply only together with --dry-run")
    if args.check and args.apply:
        ap.error("--check and --apply are exclusive")
    if executing and host.euid() != 0:
        print("host_setup: --apply needs root: sudo python3 tools/setup/host_setup.py --apply "
              "(or add --dry-run to just print the fixes)", file=sys.stderr)
        return 2

    checks = run_checks(host, args.scope, args.only)
    pending = [c for c in checks if c.status in (MISSING, WARN) and c.fix_cmds] if args.apply else []

    if args.check:
        miss = missing_list(checks)
        if miss:
            print("missing: " + "; ".join(miss), file=sys.stderr)
        return exit_code(checks)
    if args.json:
        json.dump({"checks": [c.to_dict() for c in checks], "exit": exit_code(checks),
                   "would_run": [s.display() for c in pending for s in c.fix_cmds]}, out, indent=2)
        out.write("\n")
        return exit_code(checks)
    for c in checks:
        print(format_check(c), file=out)
    if not args.apply:
        return exit_code(checks)
    if args.dry_run:
        print("\n--apply --dry-run: " + ("nothing to run." if not pending
                                         else "would run (nothing executed):"), file=out)
        for c in pending:
            print(f"\n# {c.name}", file=out)
            for s in c.fix_cmds:
                print(s.display(), file=out)
        return exit_code(checks)
    if not pending:
        print("\nnothing to apply.", file=out)

    final = {c.name: c for c in checks}
    for c in pending:
        print(f"\n# {c.name}: {c.detail}", file=out)
        for s in c.fix_cmds:
            print(s.display(), file=out)
        if not args.yes and not ask(f"Apply the {c.name} fix above? [y/N] "):
            print(f"skipped {c.name}", file=out)
            continue
        for s in c.fix_cmds:
            rc = host.execute(s)
            if rc != 0:
                print(f"command failed (exit {rc}): {s.display().splitlines()[0]}; stopping {c.name}", file=out)
                break
        again = run_checks(host, args.scope, c.name)
        if again:
            final[c.name] = again[0]
            print("re-check: " + format_check(again[0]), file=out)
    return exit_code(list(final.values()))


if __name__ == "__main__":
    sys.exit(main())
