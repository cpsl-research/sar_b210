"""Pipeline stages 1-8 from a run folder to images.

1 load and filter, 2 average chirps, 3 range compress, 4 leakage calibration,
5 clutter removal, 6 backprojection, 7 autofocus, 8 output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from b210_sar.processing.backprojection import ImageGrid


@dataclass(frozen=True)
class PipelineOptions:
    grid: ImageGrid
    tau_leak_s: float
    range_upsample: int = 4
    autofocus: bool = True
    per_subband_images: bool = True
    use_gpu: bool = False


def process_run(run_path: str | Path, output_path: str | Path, options: PipelineOptions) -> None:
    """Write a dB magnitude image with referenced axes, plus per-sub-band debug images."""
    raise NotImplementedError("T6-T8")


def main() -> int:
    """CLI: sar-image RUN OUTPUT."""
    raise NotImplementedError("T6-T8")
