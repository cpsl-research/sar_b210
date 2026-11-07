"""Waveform and timing description of a run config, as JSON for the GUI diagram. Stdlib only.

No new maths: every number comes from `budget.compute_budget` / `budget.delay_doppler` (planned timing only).
"""

from __future__ import annotations

from b210_sar.budget import compute_budget, delay_doppler
from b210_sar.config import RunConfig, step_centres


def waveform_timeline(cfg: RunConfig, num_sweeps: int = 64) -> dict:
    """What the Configs diagram draws: per-step bands, per-step timing, and the labelled derived numbers.

    Step k has centre f_k = fc + (k-(N-1)/2)*df and band f_k +/- B/2; `chirps_per_dwell` = dwell/chirp length.
    """
    w, t, rf, band = cfg.waveform, cfg.timing, cfg.rf, cfg.permitted_band
    b = compute_budget(cfg)
    dd = delay_doppler(cfg, num_sweeps)
    steps = [{"k": k, "centre_hz": f, "lo_hz": f - w.chirp_bandwidth_hz / 2, "hi_hz": f + w.chirp_bandwidth_hz / 2}
             for k, f in enumerate(step_centres(cfg))]
    permitted = None
    if band.min_hz is not None and band.max_hz is not None:
        permitted = {"min_hz": band.min_hz, "max_hz": band.max_hz}
    return {
        "basis": "planned",
        "center_freq_hz": rf.center_freq_hz,
        "num_steps": w.num_steps,
        "steps": steps,
        "chirp_bandwidth_hz": w.chirp_bandwidth_hz,
        "step_spacing_hz": w.step_spacing_hz,
        "overlap_hz": w.chirp_bandwidth_hz - w.step_spacing_hz,
        "taper_fraction": w.taper_fraction,
        "chirps_per_dwell": round(w.dwell_s / w.chirp_length_s, 6),
        "total_band_hz": b.total_bandwidth_hz,
        "band_lo_hz": rf.center_freq_hz - b.total_bandwidth_hz / 2,
        "band_hi_hz": rf.center_freq_hz + b.total_bandwidth_hz / 2,
        "permitted_band": permitted,
        "timing": {
            "retune_s": t.retune_s,
            "wait_s": max(t.settle_margin_s, t.command_lead_s + w.chirp_length_s),
            "dwell_s": w.dwell_s,
            "chirp_length_s": w.chirp_length_s,
            "step_time_s": b.step_time_s,
            "sweep_time_s": b.sweep_time_s,
        },
        "derived": {
            "range_resolution_m": dd.range_resolution_m,
            "max_unambiguous_range_m": dd.max_unambiguous_range_m,
            "max_unambiguous_velocity_mps": dd.velocity_span_mps,
            "velocity_resolution_mps": dd.velocity_resolution_mps,
            "num_sweeps": num_sweeps,
        },
    }
