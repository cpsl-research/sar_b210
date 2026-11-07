"""Parameter budget and delay-Doppler limits from a run config. Stdlib only (no numpy).

`compute_budget` is a field-for-field port of `capture/src/budget.cpp`; `format_budget` reproduces the text
`sar_capture --check` prints. `delay_doppler` adds the limits of docs/delay_doppler.md section 5.

Every timing-dependent number carries `basis`:
  planned   sweep period from the config (retune_s + wait + dwell, per step, as budget.cpp), e.g. 36.40 ms
  measured  mean sweep period from a run's burst timestamps (bursts.csv usrp_time_s), e.g. 30.64 ms
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from b210_sar.config import RunConfig

SPEED_OF_LIGHT = 299792458.0
BYTES_PER_SAMPLE = 4.0  # sc16


@dataclass(frozen=True)
class Budget:
    total_bandwidth_hz: float
    range_resolution_m: float
    step_time_s: float
    sweep_time_s: float
    sweep_rate_hz: float
    wavelength_m: float
    spacing_lambda_over_3_m: float
    spacing_for_beamwidth_m: float
    max_speed_lambda_over_3_mps: float
    max_speed_for_beamwidth_mps: float
    position_accuracy_m: float
    data_per_sweep_bytes: float
    data_rate_bytes_per_s: float
    tx_duty_cycle: float
    max_avg_conducted_dbm: float  # NaN if the EIRP cap or antenna gain is unset
    max_burst_conducted_dbm: float
    ram_slots: int
    ram_bytes: float
    basis: str = "planned"  # sweep_time_s and everything derived from it come from retune_s etc.


def compute_budget(c: RunConfig) -> Budget:
    w, t = c.waveform, c.timing
    total_bw = (w.num_steps - 1) * w.step_spacing_hz + w.chirp_bandwidth_hz
    wait_s = max(t.settle_margin_s, t.command_lead_s + w.chirp_length_s)
    step_time = t.retune_s + wait_s + w.dwell_s
    sweep_time = w.num_steps * step_time
    wavelength = SPEED_OF_LIGHT / c.rf.center_freq_hz
    s3 = wavelength / 3
    sbw = wavelength / (4 * math.sin(c.antenna.beamwidth_deg * math.pi / 360))
    per_sweep = w.num_steps * w.dwell_s * w.sample_rate_hz * BYTES_PER_SAMPLE
    duty = (w.dwell_s + w.chirp_length_s) / step_time
    avg = burst = math.nan
    if c.permitted_band.max_avg_eirp_dbm is not None and c.antenna.gain_dbi is not None:
        avg = c.permitted_band.max_avg_eirp_dbm - c.antenna.gain_dbi
        burst = avg - 10 * math.log10(duty)
    slots = math.ceil(t.max_run_s * 1.25 / step_time)
    return Budget(
        total_bandwidth_hz=total_bw,
        range_resolution_m=SPEED_OF_LIGHT / (2 * total_bw),
        step_time_s=step_time,
        sweep_time_s=sweep_time,
        sweep_rate_hz=1 / sweep_time,
        wavelength_m=wavelength,
        spacing_lambda_over_3_m=s3,
        spacing_for_beamwidth_m=sbw,
        max_speed_lambda_over_3_mps=s3 / sweep_time,
        max_speed_for_beamwidth_mps=sbw / sweep_time,
        position_accuracy_m=wavelength / 8,
        data_per_sweep_bytes=per_sweep,
        data_rate_bytes_per_s=per_sweep / sweep_time,
        tx_duty_cycle=duty,
        max_avg_conducted_dbm=avg,
        max_burst_conducted_dbm=burst,
        ram_slots=slots,
        ram_bytes=slots * w.dwell_s * w.sample_rate_hz * BYTES_PER_SAMPLE,
    )


def format_budget(c: RunConfig, b: Budget) -> str:
    """The text `sar_capture --check` prints (two decimals throughout)."""
    half = b.total_bandwidth_hz / 2
    out = [
        "Parameter budget",
        f"  occupied band        {(c.rf.center_freq_hz - half) / 1e6:.2f} - {(c.rf.center_freq_hz + half) / 1e6:.2f} MHz ({c.waveform.num_steps} steps)",
        f"  range resolution     {b.range_resolution_m:.2f} m",
        f"  sweep time           {b.sweep_time_s * 1e3:.2f} ms ({b.sweep_rate_hz:.2f} sweeps/s)",
        f"  wavelength           {b.wavelength_m * 100:.2f} cm",
        f"  max speed            {b.max_speed_lambda_over_3_mps:.2f} m/s at lambda/3, {b.max_speed_for_beamwidth_mps:.2f} m/s for {c.antenna.beamwidth_deg:.2f} deg beam",
        f"  planned speed        {c.platform.planned_speed_mps:.2f} m/s",
        f"  position accuracy    {b.position_accuracy_m * 1e3:.2f} mm relative",
        f"  data rate            {b.data_rate_bytes_per_s / 1e6:.2f} MB/s",
        f"  RAM buffer           {b.ram_bytes / 1e9:.2f} GB ({b.ram_slots} bursts)",
        f"  TX duty cycle        {b.tx_duty_cycle * 100:.2f} %",
    ]
    if not math.isnan(b.max_avg_conducted_dbm):
        out.append(f"  TX conducted limit   {b.max_avg_conducted_dbm:.2f} dBm average, {b.max_burst_conducted_dbm:.2f} "
                   "dBm during a burst if duty-cycle averaging applies")
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class DelayDoppler:
    basis: str  # planned | measured
    sweep_period_s: float  # T_s used
    num_sweeps: int  # M, sweeps per Doppler CPI
    total_bandwidth_hz: float  # B_tot = (N-1) df + B
    range_resolution_m: float  # dR = c / (2 B_tot)
    range_resolution_single_step_m: float  # c / (2 B)
    max_unambiguous_range_m: float  # c / (2 f_s / N_c) = c T_c / 2
    doppler_span_hz: float  # +/- 1 / (2 T_s)
    velocity_span_mps: float  # v_amb = +/- lambda / (4 T_s)
    doppler_resolution_hz: float  # 1 / (M T_s)
    velocity_resolution_mps: float  # dv = lambda / (2 M T_s)


def delay_doppler(c: RunConfig, num_sweeps: int = 64, sweep_period_s: float | None = None) -> DelayDoppler:
    """Delay-Doppler limits (docs/delay_doppler.md section 5).

    B_tot = (N-1) df + B;  dR = c / (2 B_tot);  R_amb = c / (2 f_s / N_c);
    v_amb = +/- lambda / (4 T_s);  dv = lambda / (2 M T_s);  Doppler span +/- 1/(2 T_s), resolution 1/(M T_s).
    `sweep_period_s` None -> planned (budget.cpp sweep time); given -> measured (mean burst-timestamp period).
    """
    w = c.waveform
    planned = sweep_period_s is None
    ts = compute_budget(c).sweep_time_s if planned else sweep_period_s
    n_c = round(w.chirp_length_s * w.sample_rate_hz)
    lam = SPEED_OF_LIGHT / c.rf.center_freq_hz
    b_tot = (w.num_steps - 1) * w.step_spacing_hz + w.chirp_bandwidth_hz
    return DelayDoppler(
        basis="planned" if planned else "measured",
        sweep_period_s=ts,
        num_sweeps=num_sweeps,
        total_bandwidth_hz=b_tot,
        range_resolution_m=SPEED_OF_LIGHT / (2 * b_tot),
        range_resolution_single_step_m=SPEED_OF_LIGHT / (2 * w.chirp_bandwidth_hz),
        max_unambiguous_range_m=SPEED_OF_LIGHT / (2 * (w.sample_rate_hz / n_c)),
        doppler_span_hz=1 / (2 * ts),
        velocity_span_mps=lam / (4 * ts),
        doppler_resolution_hz=1 / (num_sweeps * ts),
        velocity_resolution_mps=lam / (2 * num_sweeps * ts),
    )


def measured_sweep_period(run_dir: str | Path) -> dict:
    """Mean sweep period from a run's bursts.csv: usrp_time_s of step 0, sweep to sweep (valid bursts only).

    Returns {sweep_period_s, step_period_s, sweep_period_std_s, sweep_period_max_s, sweeps}.
    """
    first: dict[int, float] = {}
    times: dict[tuple[int, int], float] = {}
    with open(Path(run_dir) / "bursts.csv", newline="") as f:
        for row in csv.DictReader(f):
            if row["valid"] != "1":
                continue
            sweep, step, t = int(row["sweep"]), int(row["step"]), float(row["usrp_time_s"])
            times[(sweep, step)] = t
            if step == 0:
                first[sweep] = t
    sweeps = sorted(first)
    gaps = [(first[b] - first[a]) / (b - a) for a, b in zip(sweeps, sweeps[1:])]
    if not gaps:
        raise ValueError(f"{run_dir}: need at least two sweeps with a valid step-0 burst")
    mean = sum(gaps) / len(gaps)
    std = math.sqrt(sum((g - mean) ** 2 for g in gaps) / len(gaps))
    steps = [times[(s, 1)] - times[(s, 0)] for s in sweeps if (s, 0) in times and (s, 1) in times]
    return {"sweep_period_s": mean, "step_period_s": sum(steps) / len(steps) if steps else None,
            "sweep_period_std_s": std, "sweep_period_max_s": max(gaps), "sweeps": len(sweeps)}


def derived_numbers(c: RunConfig, run_dir: str | Path | None = None, num_sweeps: int = 64) -> dict:
    """Budget plus delay-Doppler numbers, each labelled with its basis. `measured` only when run_dir is given."""
    out = {"budget": asdict(compute_budget(c)), "delay_doppler": {"planned": asdict(delay_doppler(c, num_sweeps))}}
    if run_dir is not None:
        m = measured_sweep_period(run_dir)
        out["measured_run"] = m
        out["delay_doppler"]["measured"] = asdict(delay_doppler(c, num_sweeps, m["sweep_period_s"]))
    return out
