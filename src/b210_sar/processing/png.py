"""Minimal dB image PNG with metre axes, standard library + numpy only (the venv has no matplotlib on purpose).

Grayscale over `dynamic_range_db` below the peak, pixels upscaled by nearest neighbour, tick labels in a
built-in 3x5 font. For a quick look; the numbers live in image.npz.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

_FONT = {  # 3x5 glyphs, rows top to bottom, 3 bits each
    "0": (7, 5, 5, 5, 7), "1": (2, 6, 2, 2, 7), "2": (7, 1, 7, 4, 7), "3": (7, 1, 7, 1, 7),
    "4": (5, 5, 7, 1, 1), "5": (7, 4, 7, 1, 7), "6": (7, 4, 7, 5, 7), "7": (7, 1, 1, 1, 1),
    "8": (7, 5, 7, 5, 7), "9": (7, 5, 7, 1, 7), "-": (0, 0, 7, 0, 0), ".": (0, 0, 0, 0, 2),
    " ": (0, 0, 0, 0, 0), "x": (0, 5, 2, 5, 0), "y": (5, 5, 7, 1, 7), "m": (0, 7, 7, 5, 5),
    "d": (1, 1, 7, 5, 7), "B": (6, 5, 6, 5, 6), "(": (1, 2, 2, 2, 1), ")": (4, 2, 2, 2, 4),
}


def _text(canvas: np.ndarray, s: str, row: int, col: int, scale: int = 2, value: int = 0) -> None:
    for i, ch in enumerate(s):
        g = _FONT.get(ch, _FONT[" "])
        for r, bits in enumerate(g):
            for c in range(3):
                if bits >> (2 - c) & 1:
                    r0, c0 = row + r * scale, col + (i * 4 + c) * scale
                    if 0 <= r0 and r0 + scale <= canvas.shape[0] and 0 <= c0 and c0 + scale <= canvas.shape[1]:
                        canvas[r0:r0 + scale, c0:c0 + scale] = value


def _write_png(path: Path, gray: np.ndarray) -> None:
    h, w = gray.shape
    raw = b"".join(b"\x00" + gray[r].astype(np.uint8).tobytes() for r in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def _ticks(lo: float, hi: float, n: int = 5) -> np.ndarray:
    span = hi - lo
    if span <= 0:
        return np.array([lo])
    step = 10 ** np.floor(np.log10(span / n))
    for m in (1, 2, 5, 10):
        if span / (m * step) <= n:
            step *= m
            break
    return np.arange(np.ceil(lo / step) * step, hi + 1e-9 * span, step)


def write_db_png(path: str | Path, image: np.ndarray, x_m: np.ndarray, y_m: np.ndarray,
                 dynamic_range_db: float = 40.0, target_px: int = 600) -> None:
    """Write |image| in dB (0 dB = peak, black at -dynamic_range_db) with x across and y up, in metres."""
    a = np.abs(np.asarray(image))
    db = 20 * np.log10(a / max(float(a.max()), 1e-300) + 1e-300)
    g = np.clip((db + dynamic_range_db) / dynamic_range_db, 0, 1) * 255
    g = g[::-1]  # y increases upward
    ny, nx = g.shape
    sx = max(1, target_px // max(nx, 1))
    sy = max(1, target_px // max(ny, 1))
    img = np.kron(g, np.ones((sy, sx)))
    ml, mb, mt, mr = 70, 40, 10, 10
    h, w = img.shape
    canvas = np.full((h + mt + mb, w + ml + mr), 255.0)
    canvas[mt:mt + h, ml:ml + w] = img
    x0, x1 = float(x_m[0]), float(x_m[-1])
    y0, y1 = float(y_m[0]), float(y_m[-1])
    for t in _ticks(x0, x1):
        c = ml + int(round((t - x0) / (x1 - x0 or 1) * (w - 1)))
        canvas[mt + h:mt + h + 5, c] = 0
        _text(canvas, f"{t:g}", mt + h + 8, c - 8)
    for t in _ticks(y0, y1):
        r = mt + h - 1 - int(round((t - y0) / (y1 - y0 or 1) * (h - 1)))
        canvas[r, ml - 5:ml] = 0
        _text(canvas, f"{t:g}", r - 4, 8)
    _text(canvas, "x (m)", mt + h + 24, ml + w // 2 - 20)
    _text(canvas, "y (m)", mt + h + 24, 2)
    _write_png(Path(path), canvas)
