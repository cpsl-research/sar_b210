"""FastAPI app: /api/health, /api/state, /api/configs*, WebSocket /stream, static web/."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .configs_api import make_router
from .hub import Hub
from .sources import make_source

WEB = Path(__file__).resolve().parent / "web"


class _NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app(source: str = "none", config_root: str | os.PathLike | None = None) -> FastAPI:
    hub = Hub()
    src = make_source(source)
    @asynccontextmanager
    async def lifespan(_app):
        hub.bind_loop(asyncio.get_running_loop())
        src.start(hub)
        try:
            yield
        finally:
            src.stop()

    app = FastAPI(title="b210_sar GUI", lifespan=lifespan)
    app.state.hub = hub

    app.include_router(make_router(config_root))

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/state")
    def state():
        return {"source": src.name, "status": hub.status, "frames_sent": hub.frames, "clients": len(hub.clients)}

    @app.websocket("/stream")
    async def stream(ws: WebSocket):
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        q.put_nowait(hub.status)
        hub.clients.add(q)
        try:
            while True:
                await ws.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.clients.discard(q)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/", _NoCacheStatic(directory=str(WEB)), name="web")
    return app
