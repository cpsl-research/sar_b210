"""FastAPI app: /api/health, /api/state, /api/configs*, WebSocket /stream, static web/."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import filesource
from .configs_api import make_router
from .hub import Hub
from .sources import make_source  # noqa: E402

WEB = Path(__file__).resolve().parent / "web"


class _NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(source: str = "none", config_root: str | os.PathLike | None = None,
               data_roots=None, path: str | None = None) -> FastAPI:
    """`data_roots`: directories the file picker may open from (default runs/ and ~/Documents/console/data).
    `path`: a file to open at start (--source file)."""
    hub = Hub()
    if source == "file":
        fs = make_source("file", data_roots=data_roots, path=path)
        src = fs
    else:
        fs = filesource.FileSource(data_roots=data_roots)  # idle until a file is opened from the UI
        fs.announce = False
        src = make_source(source)
    @asynccontextmanager
    async def lifespan(_app):
        hub.bind_loop(asyncio.get_running_loop())
        src.start(hub)
        if fs is not src:
            fs.start(hub)
        try:
            yield
        finally:
            src.stop()
            if fs is not src:
                fs.stop()

    app = FastAPI(title="b210_sar GUI", lifespan=lifespan)
    app.state.hub = hub
    app.state.file = fs

    app.include_router(make_router(config_root))

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/state")
    def state():
        return {"source": fs.name if fs.path else src.name, "status": hub.status, "frames_sent": hub.frames, "clients": len(hub.clients)}

    @app.websocket("/stream")
    async def stream(ws: WebSocket):
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        q.put_nowait(hub.status)
        for m in fs.replay():
            q.put_nowait(m)
        hub.clients.add(q)
        try:
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.clients.discard(q)

    # -- file view
    def _guard(fn, *args):
        try:
            return fn(*args)
        except filesource.PathRefused as e:
            raise HTTPException(403, str(e))
        except FileNotFoundError:
            raise HTTPException(404, "not found")
        except (ValueError, KeyError, OSError, ImportError) as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}")

    @app.get("/api/files")
    def files(path: str = ""):
        return _guard(fs.roots.listing, path)

    @app.get("/api/file")
    def file_state():
        return {"state": fs.state(), "meta": fs._meta, "frame": fs.last_frame}

    @app.post("/api/open")
    def open_file(body: dict = Body(...)):
        p = body.pop("path", None)
        if not p:
            raise HTTPException(400, "path required")
        return _guard(lambda: fs.open(p, **body))

    @app.post("/api/control")
    def control(body: dict = Body(...)):
        return _guard(fs.control, body)

    @app.websocket("/dd")
    async def dd_ws(ws: WebSocket):
        """Delay-Doppler maps, binary, newest wins: a slow client skips maps instead of queueing them."""
        await ws.accept()
        ev = fs.latest.subscribe()
        seen = 0
        try:
            while True:
                if fs.latest.seq == seen:
                    ev.clear()
                    if fs.latest.seq == seen:
                        await ev.wait()
                data, seen = fs.latest.data, fs.latest.seq
                if data is not None:
                    await ws.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            fs.latest.unsubscribe(ev)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/", _NoCacheStatic(directory=str(WEB)), name="web")
    return app
