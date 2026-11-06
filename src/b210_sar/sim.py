"""T9: synthetic runs for testing processing before field data exists.

Generates point targets along a noisy trajectory with random per-step LO phases and a leakage
path, written in the capture dataset layout with antenna positions already attached.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from b210_sar.config import RunConfig
from b210_sar.dataset import Run


@dataclass(frozen=True)
class SimScene:
    targets_m: npt.NDArray[np.float64]  # shape (num_targets, 3)
    amplitudes: npt.NDArray[np.float64]  # shape (num_targets,)
    speed_mps: float
    path_error_std_m: float
    leakage_delay_s: float
    leakage_amplitude: float
    noise_std: float
    seed: int


def simulate_run(config: RunConfig, scene: SimScene) -> Run:
    """Simulate a complete run."""
    raise NotImplementedError("T9")


def main() -> int:
    """CLI: sar-sim CONFIG SCENE OUTPUT."""
    raise NotImplementedError("T9")
