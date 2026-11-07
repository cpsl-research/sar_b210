"""File view: a run folder or console HDF5 recording -> processing (b210-13) -> Hub / binary WebSocket.

`FileSource` owns one open file at a time. A frame is the window of M sweeps ending at position `pos`:
JSON `frame` messages (metadata + range profile of sweep `pos`) go through the Hub, the delay-Doppler map
(float32 dB) goes to `Latest` (newest wins) for the binary WebSocket `/dd`. Paths are confined to the
data roots (resolved, so `..` and symlink escapes are refused).
"""

from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from b210_sar.dataset import open_source
from b210_sar.processing import delay_doppler as dd

from .hub import Hub
from .sources import Source, register

REPO = Path(__file__).resolve().parents[3]


class PathRefused(ValueError):
    pass


def default_data_roots() -> list[Path]:
    out = []
    for c in (REPO / "runs", Path.home() / "Documents" / "console" / "data"):
        if c.is_dir():
            out.append(c)
    return out


class Roots:
    def __init__(self, roots) -> None:
        self.roots = [Path(r).expanduser().resolve() for r in roots]

    def check(self, path: str | Path) -> Path:
        """Resolve `path` (absolute, or relative to a root) and require it to be inside a root."""
        p = Path(str(path)).expanduser()
        cands = [p] if p.is_absolute() else [r / p for r in self.roots]
        for c in cands:
            try:
                rp = c.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if any(rp == r or r in rp.parents for r in self.roots):
                return rp
        if any(c.exists() for c in cands):
            raise PathRefused("path is outside the data roots")
        raise FileNotFoundError(str(path))

    def listing(self, path: str | None) -> dict:
        if not path:
            return {"path": "", "parent": None, "entries": [
                {"name": str(r), "path": str(r), "kind": "dir"} for r in self.roots]}
        d = self.check(path)
        if not d.is_dir():
            raise NotADirectoryError(str(path))
        entries = []
        for e in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
            try:
                rp = e.resolve(strict=True)
            except OSError:
                continue
            if not any(rp == r or r in rp.parents for r in self.roots):
                continue  # symlink pointing outside: not offered
            if rp.is_dir():
                run = (rp / "bursts.csv").is_file() or (rp / "iq.sc16").is_file()
                entries.append({"name": e.name, "path": str(rp), "kind": "run" if run else "dir"})
            elif e.suffix.lower() in (".h5", ".hdf5"):
                entries.append({"name": e.name, "path": str(rp), "kind": "h5"})
        parent = d.parent
        inside = any(parent == r or r in parent.parents for r in self.roots)
        return {"path": str(d), "parent": str(parent) if inside else None, "entries": entries}


class Latest:
    """Newest-wins slot for the binary WebSocket: set() from any thread, each client sees only the latest."""

    def __init__(self) -> None:
        self.data: bytes | None = None
        self.seq = 0
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
        self._lock = threading.Lock()

    def set(self, data: bytes) -> None:
        with self._lock:
            self.data = data
            self.seq += 1
            waiters = list(self._waiters)
        for loop, ev in waiters:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                pass

    def subscribe(self) -> asyncio.Event:
        ev = asyncio.Event()
        with self._lock:
            self._waiters.append((asyncio.get_running_loop(), ev))
        return ev

    def unsubscribe(self, ev: asyncio.Event) -> None:
        with self._lock:
            self._waiters = [w for w in self._waiters if w[1] is not ev]


def pack_map(header: dict, db: np.ndarray) -> bytes:
    """uint32 LE header length, JSON header, float32 LE dB map (num_doppler x num_ranges, row 0 = lowest velocity)."""
    h = json.dumps(header).encode()
    return struct.pack("<I", len(h)) + h + np.ascontiguousarray(db, dtype="<f4").tobytes()


def _fl(a, nd=2):
    return np.round(np.asarray(a, dtype=float), nd).tolist()


class FileSource(Source):
    name = "file"
    announce = True  # the idle ride-along instance (source none) leaves the hub status alone

    def __init__(self, data_roots=None, path: str | None = None, **_) -> None:
        self.roots = Roots(default_data_roots() if data_roots is None else data_roots)
        self.initial = path
        self.latest = Latest()
        self.hub: Hub | None = None
        self.src = None
        self.proc: dd.Processor | None = None
        self.path: str | None = None
        self.params = {"m": 64, "window": "hann", "hop": 1, "rate_hz": 5.0, "remove_static": False,
                       "max_range_m": 300.0, "leakage_m": None}
        self.pos = 0
        self.playing = False
        self.error = ""
        self._ids: list[int] = []
        self._cache: OrderedDict[int, dd.SweepSpectra] = OrderedDict()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0
        self._meta: dict | None = None
        self.last_frame: dict | None = None

    # -- Source interface
    def start(self, hub: Hub) -> None:
        self.hub = hub
        self._thread = threading.Thread(target=self._run, daemon=True, name="file-player")
        self._thread.start()
        if self.initial:
            try:
                self.open(self.initial)
            except Exception as e:  # shown in the UI, not fatal
                self.error = f"{type(e).__name__}: {e}"
                self._status("idle", self.error)
        elif self.announce:
            self._status("idle", "no file open")

    def stop(self) -> None:
        self._stop.set()
        self.playing = False
        if self.src is not None:
            self.src.close()

    # -- helpers
    def _status(self, state: str, msg: str) -> None:
        if self.hub:
            self.hub.set_status({"type": "status", "source": self.name, "state": state, "msg": msg})

    def state(self) -> dict:
        return {"open": self.path is not None, "path": self.path, "pos": self.pos, "n": len(self._ids),
                "playing": self.playing, "params": dict(self.params), "error": self.error,
                "roots": [str(r) for r in self.roots.roots]}

    # -- open / control
    def open(self, path: str, **params) -> dict:
        p = self.roots.check(path)
        with self._lock:
            self.playing = False
            if self.src is not None:
                self.src.close()
                self.src = None
            self.error = ""
            src = open_source(p)
            self.params.update({k: v for k, v in params.items() if k in self.params and v is not None})
            self.src, self.path = src, str(p)
            self._ids = list(src.sweep_ids)
            self._cache.clear()
            self.latest.data = None
            self._meta = None
            self.last_frame = None
            self._build_processor()
            self.pos = min(max(int(self.params["m"]) - 1, 0), max(len(self._ids) - 1, 0))
            self._publish_meta()
            self._emit()
            self._status("file", f"{p.name}: {src.report().summary()}")
        return self.state()

    def _build_processor(self) -> None:
        self.proc = None
        src = self.src
        if src.config is None:
            self.error = "no run configuration in this file: cannot process (metadata only)"
            return
        if not self._ids:
            self.error = "no sweeps in this file"
            return
        try:
            proc = dd.Processor(src.config, replica=getattr(src, "replica", None),
                                min_range_m=0.0, max_range_m=float(self.params["max_range_m"]))
            lk = self.params.get("leakage_m")
            if lk is not None:
                tau = float(lk) / dd.SPEED_OF_LIGHT
                first = [src.sweep(i) for i in self._ids[: min(16, len(self._ids))]]
                proc.cal = dd.fit_leakage_cal(first, proc, tau)
            self.proc = proc
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"

    def control(self, body: dict) -> dict:
        with self._lock:
            if self.src is None:
                raise ValueError("no file open")
            act = body.get("action")
            rebuild = False
            for k in ("m", "hop"):
                if k in body:
                    self.params[k] = max(int(body[k]), 1 if k == "hop" else 2)
            if "window" in body:
                if body["window"] not in ("hann", "hamming", "rect"):
                    raise ValueError("window must be hann, hamming or rect")
                self.params["window"] = body["window"]
            if "rate_hz" in body:
                self.params["rate_hz"] = min(max(float(body["rate_hz"]), 0.2), 10.0)
            if "remove_static" in body:
                self.params["remove_static"] = bool(body["remove_static"])
            if "max_range_m" in body:
                self.params["max_range_m"] = min(max(float(body["max_range_m"]), 5.0), 2000.0)
                rebuild = True
            if "leakage_m" in body:
                v = body["leakage_m"]
                self.params["leakage_m"] = None if v in (None, "") else float(v)
                rebuild = True
            if rebuild:
                self._cache.clear()
                self._build_processor()
                self._publish_meta()
            n = len(self._ids)
            lo = min(int(self.params["m"]) - 1, n - 1)
            if act == "play":
                if self.pos >= n - 1:
                    self.pos = lo
                self.playing = True
            elif act == "pause":
                self.playing = False
            elif act == "step":
                self.playing = False
                self.pos = min(self.pos + int(body.get("delta", self.params["hop"])), n - 1)
            elif act == "back":
                self.playing = False
                self.pos = max(self.pos - int(body.get("delta", self.params["hop"])), lo)
            elif act == "seek":
                self.pos = min(max(int(body["pos"]), lo), n - 1)
            elif act not in (None, "set"):
                raise ValueError(f"unknown action {act!r}")
            self.pos = min(max(self.pos, lo), max(n - 1, 0))
            self._emit()
            return self.state()

    # -- processing
    def _spectra(self, pos: int) -> dd.SweepSpectra:
        sid = self._ids[pos]
        sp = self._cache.get(sid)
        if sp is None:
            sp = self.proc.sweep_spectra(self.src.sweep(sid))
            self._cache[sid] = sp
            limit = max(int(self.params["m"]) + 8, 72)
            while len(self._cache) > limit:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(sid)
        return sp

    def _publish_meta(self) -> None:
        src, proc = self.src, self.proc
        rep = src.report()
        meta = {"type": "file", "path": self.path, "name": Path(self.path).name, "n": len(self._ids),
                "report": {"summary": rep.summary(), "ok": rep.ok, "num_bursts": rep.num_bursts,
                           "num_sweeps": rep.num_sweeps, "complete_sweeps": rep.complete_sweeps,
                           "expected_steps": rep.expected_steps, "iq_source": rep.iq_source,
                           "config_known": rep.config_known,
                           "incomplete": {str(k): v for k, v in list(rep.incomplete.items())[:50]},
                           "incomplete_count": len(rep.incomplete),
                           "invalid_bursts": rep.invalid_bursts[:50], "invalid_count": len(rep.invalid_bursts),
                           "error_flag_count": len(rep.error_flag_bursts),
                           "duplicates": len(rep.duplicates), "unexpected": len(rep.unexpected),
                           "truncated": len(rep.truncated), "notes": list(rep.notes)},
                "error": self.error, "sweep_ids": [self._ids[0], self._ids[-1]] if self._ids else [],
                "calibrated": bool(proc is not None and proc.cal is not None and proc.cal.enabled),
                "params": dict(self.params)}
        if proc is not None:
            meta["ranges_m"] = _fl(proc.ranges_m, 2)
            meta["range_resolution_m"] = proc.range_resolution_m
            meta["total_bandwidth_hz"] = proc.total_bandwidth_hz
            meta["max_unambiguous_range_m"] = proc.max_unambiguous_range_m
            meta["wavelength_m"] = proc.wavelength_m
            meta["num_steps"] = proc.num_steps
        t = np.asarray(src.bursts["usrp_time_s"], float) if "usrp_time_s" in (src.bursts.dtype.names or ()) else None
        self._t0 = float(np.nanmin(t)) if t is not None and t.size else 0.0
        self._meta = meta
        if self.hub:
            self.hub.publish(meta, count=False)

    def _emit(self) -> None:
        """Compute the frame at self.pos and publish it. Errors are reported in the frame, never swallowed."""
        if self.src is None or not self._ids:
            return
        n = len(self._ids)
        frame = {"type": "frame", "pos": self.pos, "n": n, "sweep": self._ids[self.pos],
                 "playing": self.playing, "m": int(self.params["m"]), "window": self.params["window"],
                 "error": self.error}
        proc = self.proc
        if proc is None:
            if self.hub:
                self.hub.publish(frame)
            return
        try:
            cur = self._spectra(self.pos)
            frame["valid_steps"] = [bool(v) for v in cur.valid]
            frame["sweep_complete"] = bool(cur.valid.all())
            rp = dd.range_profile(cur, proc)
            pdb = 20 * np.log10(np.abs(rp.profile) + 1e-12)
            frame["profile_db"] = _fl(pdb, 1)
            frame["profile_peak"] = {"range_m": float(rp.ranges_m[int(np.argmax(pdb))]),
                                     "db": float(pdb.max())}
            m = int(self.params["m"])
            start = self.pos - m + 1
            if start >= 0:
                win = [self._spectra(i) for i in range(start, self.pos + 1)]
                mp = dd.delay_doppler_map(win, self.params["window"], processor=proc,
                                          remove_static=bool(self.params["remove_static"]))
                db = mp.power_db()
                frame.update({"cpi_sweeps": [int(mp.sweeps[0]), int(mp.sweeps[-1])],
                              "missing_bursts": mp.missing_bursts,
                              "sweep_period_ms": mp.sweep_period_s * 1e3, "t_s": mp.t_mid_s - self._t0,
                              "velocity_resolution_mps": mp.velocity_resolution_mps,
                              "velocity_span_mps": mp.velocity_span_mps,
                              "doppler_resolution_hz": mp.doppler_resolution_hz,
                              "basis": "measured (mean burst-timestamp sweep period of this window)"})
                hdr = {"pos": self.pos, "sweep": self._ids[self.pos], "shape": list(db.shape),
                       "ranges_m": [float(mp.ranges_m[0]), float(mp.ranges_m[-1])],
                       "velocity_mps": _fl(mp.velocity_mps, 4), "zero_index": mp.zero_doppler_index,
                       "db_max": float(db.max()), "t_s": mp.t_mid_s - self._t0}
                self.latest.set(pack_map(hdr, db))
            else:
                frame["map_note"] = f"window needs {m} sweeps; at sweep {self.pos + 1} of {n}"
        except Exception as e:
            frame["error"] = f"{type(e).__name__}: {e}"
        self.last_frame = frame
        if self.hub:
            self.hub.publish(frame)

    def replay(self) -> list[dict]:
        """Messages a newly connected /stream client needs to catch up."""
        return [m for m in (self._meta, self.last_frame) if m]

    # -- player thread
    def _run(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            if not self.playing or self.src is None:
                time.sleep(0.05)
                nxt = time.monotonic()
                continue
            now = time.monotonic()
            if now < nxt:
                time.sleep(min(nxt - now, 0.05))
                continue
            with self._lock:
                if self.playing:
                    n = len(self._ids)
                    self.pos = min(self.pos + int(self.params["hop"]), n - 1)
                    if self.pos >= n - 1:
                        self.playing = False
                    self._emit()
            nxt += 1.0 / max(float(self.params["rate_hz"]), 0.1)
            if nxt < time.monotonic() - 1:
                nxt = time.monotonic()


register("file", lambda **kw: FileSource(**kw))
