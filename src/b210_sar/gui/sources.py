"""Data sources for the GUI. A Source pushes JSON-able messages into the Hub.

Only `none` exists now. File sources (b210-15) and a ROS source (b210-16) register here with
`register(name, factory)` and need no change to app.py or hub.py.
"""

from __future__ import annotations

from typing import Callable

from .hub import Hub


class Source:
    name = "base"
    rate_hz = 0.0

    def start(self, hub: Hub) -> None:  # pragma: no cover - interface
        pass

    def stop(self) -> None:  # pragma: no cover - interface
        pass


class NoneSource(Source):
    """No data: the GUI shows the Configs tab and waits."""

    name = "none"

    def start(self, hub: Hub) -> None:
        hub.set_status({"type": "status", "source": self.name, "state": "idle",
                        "msg": "no data source (--source none)"})


_REGISTRY: dict[str, Callable[..., Source]] = {"none": lambda **_: NoneSource()}


def register(name: str, factory: Callable[..., Source]) -> None:
    _REGISTRY[name] = factory


def source_names() -> list[str]:
    return sorted(_REGISTRY)


def make_source(name: str, **kw) -> Source:
    if name not in _REGISTRY:
        raise ValueError(f"unknown source {name!r}; available: {', '.join(source_names())}")
    return _REGISTRY[name](**kw)
