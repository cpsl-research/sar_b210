"""Fan-out of source messages to WebSocket clients. Slow clients drop their oldest message."""

from __future__ import annotations

import asyncio
import threading


class Hub:
    def __init__(self) -> None:
        self.status: dict = {"type": "status", "source": "none", "state": "idle", "msg": ""}
        self.clients: set[asyncio.Queue] = set()
        self.frames = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_status(self, status: dict) -> None:
        self.status = status
        self.publish(status, count=False)

    def publish(self, msg: dict, count: bool = True) -> None:
        """Thread-safe: callable from a source's worker thread or from the event loop."""
        if count:
            with self._lock:
                self.frames += 1
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(msg)
        else:
            loop.call_soon_threadsafe(self._fanout, msg)

    def _fanout(self, msg: dict) -> None:
        for q in list(self.clients):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)
