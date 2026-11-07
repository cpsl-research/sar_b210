"""Figures and numbers for docs/theory/stepped_frequency_primer.md (b210-26 Step 1).

Run with a python that has numpy, matplotlib and PyYAML (the sar_b210 venv has no matplotlib on purpose):

    python3 docs/figures/stepped_frequency_primer_figures.py [RUN_DIR]

Writes fig_sfp_*.png next to this file and prints a "numbers quoted" block. Every number the primer
quotes is computed here from `b210_sar.budget` on `configs/default.yaml` (or an independent formula
that is asserted equal to it). The measured sweep period comes from RUN_DIR/bursts.csv through
`measured_sweep_period` (default: $B210_RUN_DIR, else the console data/b210_runs run named in the
primer); with no run folder it falls back to the recorded 30.637 ms and says so.
"""

from __future__ import annotations

import dataclasses
import math
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
sys.path.insert(0, str(ROOT / "src"))
from b210_sar.budget import compute_budget, delay_doppler, measured_sweep_period  # noqa: E402
from b210_sar.config import load_config  # noqa: E402

CFG = load_config(ROOT / "configs" / "default.yaml")
W, T = CFG.waveform, CFG.timing
C = 299792458.0
M = 64
INK, MUTED = "#2b2b2b", "#8a8a85"
BLUE, ORANGE, GREEN, GOLD = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": INK, "text.color": INK, "axes.labelcolor": INK,
                     "xtick.color": INK, "ytick.color": INK})

# ---- numbers from the code -------------------------------------------------------------------
BUD = compute_budget(CFG)
DD_PLAN = delay_doppler(CFG, M)
RUN = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "B210_RUN_DIR", str(Path.home() / "Documents/console/data/b210_runs/run_20261106_155516_rx")))
if (RUN / "bursts.csv").exists():
    MEAS = measured_sweep_period(RUN)
    TS_MEAS, TSTEP_MEAS, MEAS_SRC = MEAS["sweep_period_s"], MEAS["step_period_s"], f"{RUN.name}/bursts.csv"
else:
    TS_MEAS, TSTEP_MEAS, MEAS_SRC = 0.030637, 0.015320, "recorded value (run folder not found)"
DD_MEAS = delay_doppler(CFG, M, TS_MEAS)

# independent re-derivation (asserted equal to the code)
LAM = C / CFG.rf.center_freq_hz
B_TOT = (W.num_steps - 1) * W.step_spacing_hz + W.chirp_bandwidth_hz
WAIT = max(T.settle_margin_s, T.command_lead_s + W.chirp_length_s)
STEP = T.retune_s + WAIT + W.dwell_s
TS_PLAN = W.num_steps * STEP
N_CHIRPS = round(W.dwell_s / W.chirp_length_s)
SLOPE = W.chirp_bandwidth_hz / W.chirp_length_s
assert math.isclose(B_TOT, BUD.total_bandwidth_hz) and math.isclose(B_TOT, DD_PLAN.total_bandwidth_hz)
assert math.isclose(C / (2 * B_TOT), BUD.range_resolution_m)
assert math.isclose(C * W.chirp_length_s / 2, DD_PLAN.max_unambiguous_range_m, rel_tol=1e-9)
assert math.isclose(TS_PLAN, BUD.sweep_time_s) and math.isclose(TS_PLAN, DD_PLAN.sweep_period_s)
assert math.isclose(LAM / (4 * TS_PLAN), DD_PLAN.velocity_span_mps)
assert math.isclose(LAM / (2 * M * TS_MEAS), DD_MEAS.velocity_resolution_mps)
assert math.isclose(LAM / (4 * TS_MEAS), DD_MEAS.velocity_span_mps)
assert abs(round(TS_PLAN * 1e3, 2) - 36.40) < 1e-9 and abs(round(TS_MEAS * 1e3, 3) - 30.637) < 1e-9, \
    f"measured sweep {TS_MEAS * 1e3:.3f} ms is not the 30.637 ms the primer quotes"
assert round(DD_PLAN.velocity_span_mps, 2) == 0.84 and round(DD_MEAS.velocity_span_mps, 2) == 1.00
assert round(DD_MEAS.velocity_resolution_mps * 100, 1) == 3.1 and round(DD_PLAN.range_resolution_m, 2) == 2.00


def with_waveform(**kw):
    return dataclasses.replace(CFG, waveform=dataclasses.replace(W, **kw))


def savefig(fig, name):
    fig.savefig(OUT / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


MHZ = 1e-6
# ---- Fig 1: one chirp, TX vs delayed echo, beat ----------------------------------------------
tau_show = 0.25 * W.chirp_length_s  # exaggerated delay so the picture is readable
t = np.linspace(0, 1.6, 800) * W.chirp_length_s
tx = np.where(t <= W.chirp_length_s, -W.chirp_bandwidth_hz / 2 + SLOPE * t, np.nan)
te = t - tau_show
rx = np.where((te >= 0) & (te <= W.chirp_length_s), -W.chirp_bandwidth_hz / 2 + SLOPE * te, np.nan)
fig, ax = plt.subplots(figsize=(6.2, 2.6))
ax.plot(t * 1e6, tx * MHZ, color=BLUE, lw=2, label="transmit")
ax.plot(t * 1e6, rx * MHZ, color=ORANGE, lw=2, label="echo (delayed by $\\tau$)")
tm = 0.55 * W.chirp_length_s
ax.annotate("", xy=(tm * 1e6, (-W.chirp_bandwidth_hz / 2 + SLOPE * (tm - tau_show)) * MHZ),
            xytext=(tm * 1e6, (-W.chirp_bandwidth_hz / 2 + SLOPE * tm) * MHZ),
            arrowprops=dict(arrowstyle="<->", color=INK))
ax.text(tm * 1e6 + 0.4, 0.5 * (-W.chirp_bandwidth_hz + SLOPE * (2 * tm - tau_show)) * MHZ,
        "beat $= (B/T_c)\\,\\tau = 2(B/T_c)R/c$", va="center", fontsize=8)
ax.annotate("", xy=((tau_show) * 1e6, -W.chirp_bandwidth_hz / 2 * MHZ - 4), xytext=(0, -W.chirp_bandwidth_hz / 2 * MHZ - 4),
            arrowprops=dict(arrowstyle="<->", color=MUTED))
ax.text(tau_show * 1e6 / 2, -W.chirp_bandwidth_hz / 2 * MHZ - 9, "$\\tau$ (exaggerated)", ha="center", fontsize=8, color=MUTED)
ax.set_xlabel("time within the chirp ($\\mu$s)")
ax.set_ylabel("offset from step centre (MHz)")
ax.set_ylim(-W.chirp_bandwidth_hz / 2 * MHZ - 14, W.chirp_bandwidth_hz / 2 * MHZ + 6)
ax.legend(frameon=False, loc="upper left", fontsize=8)
ax.set_title(f"One chirp: $B$ = {W.chirp_bandwidth_hz * MHZ:.0f} MHz over $T_c$ = {W.chirp_length_s * 1e6:.0f} $\\mu$s", fontsize=9)
savefig(fig, "fig_sfp_chirp.png")

# ---- Fig 2: sub-bands and stitched span -------------------------------------------------------
centres = (np.arange(W.num_steps) - (W.num_steps - 1) / 2) * W.step_spacing_hz
fig, ax = plt.subplots(figsize=(6.2, 2.2))
for k, (fc, col) in enumerate(zip(centres, [BLUE, ORANGE, GREEN, GOLD])):
    lo, hi = fc - W.chirp_bandwidth_hz / 2, fc + W.chirp_bandwidth_hz / 2
    ax.barh(1.0 - 0.35 * k, (hi - lo) * MHZ, left=lo * MHZ, height=0.28, color=col, alpha=0.55)
    ax.text(fc * MHZ, 1.0 - 0.35 * k, f"step {k}", ha="center", va="center", fontsize=8)
ov_lo, ov_hi = (centres[1] - W.chirp_bandwidth_hz / 2) * MHZ, (centres[0] + W.chirp_bandwidth_hz / 2) * MHZ
ax.axvspan(ov_lo, ov_hi, color=MUTED, alpha=0.2)
ax.text((ov_lo + ov_hi) / 2, 0.15, f"overlap {W.chirp_bandwidth_hz * MHZ - W.step_spacing_hz * MHZ:.0f} MHz", ha="center", fontsize=8, color=MUTED)
lo_all, hi_all = (centres[0] - W.chirp_bandwidth_hz / 2) * MHZ, (centres[-1] + W.chirp_bandwidth_hz / 2) * MHZ
ax.annotate("", xy=(hi_all, 1.45), xytext=(lo_all, 1.45), arrowprops=dict(arrowstyle="<->", color=INK))
ax.text((lo_all + hi_all) / 2, 1.52, f"stitched $B_{{tot}}$ = ({W.num_steps}-1)$\\cdot${W.step_spacing_hz * MHZ:.0f} + {W.chirp_bandwidth_hz * MHZ:.0f} = {B_TOT * MHZ:.0f} MHz",
        ha="center", fontsize=8.5)
ax.set_xlim(lo_all - 3, hi_all + 3)
ax.set_ylim(0, 1.8)
ax.set_yticks([])
ax.spines["left"].set_visible(False)
ax.set_xlabel("RF offset from band centre (MHz)")
ax.set_title("Two sub-bands stitched into one wider band", fontsize=9)
savefig(fig, "fig_sfp_stitch.png")

# ---- Fig 3: range profile, df < B vs df > B (grating lobes) ----------------------------------
NFFT, FSPAN = 2 ** 17, 6.4e9  # zero-padded IFFT: 6.4 GHz frequency window, 0.023 m range grid


def profile(step_spacing_hz, n_steps=2):
    """Noise-free point target at R=0: flat (rectangular) spectrum on the union of the sub-bands, zero-padded
    IFFT. Illustrates where the gaps put the lobes; not the Hann-windowed processing chain."""
    f = np.arange(-NFFT // 2, NFFT // 2) * (FSPAN / NFFT)
    mask = np.zeros_like(f, bool)
    for fc in (np.arange(n_steps) - (n_steps - 1) / 2) * step_spacing_hz:
        mask |= np.abs(f - fc) <= W.chirp_bandwidth_hz / 2
    p = np.abs(np.fft.fftshift(np.fft.ifft(np.fft.ifftshift(mask.astype(float)))))
    r = np.arange(-NFFT // 2, NFFT // 2) / FSPAN * C / 2
    return r, 20 * np.log10(np.maximum(p / p.max(), 1e-6)), mask


r1, p1, m1 = profile(W.step_spacing_hz)
spacing_gap = 60e6  # 15 MHz gap between the sub-bands (Delta f > B)
r2, p2, m2 = profile(spacing_gap)
sel = np.abs(r1) < 12
fig, axs = plt.subplots(1, 2, figsize=(6.4, 2.6), sharey=True)
for ax, r, p, ds, ttl, col in [(axs[0], r1, p1, W.step_spacing_hz, "$\\Delta f \\leq B$: contiguous", BLUE),
                               (axs[1], r2, p2, spacing_gap, "$\\Delta f > B$: gap in the band", ORANGE)]:
    ax.plot(r[sel], p[sel], color=col, lw=1.4)
    ax.set_title(f"{ttl}\n$\\Delta f$ = {ds * MHZ:.0f} MHz, $B$ = {W.chirp_bandwidth_hz * MHZ:.0f} MHz", fontsize=8.5)
    ax.set_xlabel("range offset from the target (m)")
    ax.set_ylim(-45, 3)
axs[0].set_ylabel("level (dB)")
gl = C / (2 * spacing_gap)
axs[1].axvline(gl, color=MUTED, ls=":", lw=1)
axs[1].axvline(-gl, color=MUTED, ls=":", lw=1)
axs[1].text(gl + 0.2, -8, f"$c/2\\Delta f$ = {gl:.2f} m", fontsize=7.5, color=MUTED)
savefig(fig, "fig_sfp_grating.png")
# the quoted grating-lobe level: the highest point of the gapped profile within 0.4 m of c/(2 df)
sel2 = np.abs(np.abs(r2) - gl) < 0.4
GL_LEVEL = p2[sel2].max()
GL_POS = abs(r2[sel2][np.argmax(p2[sel2])])
PSL1 = p1[(np.abs(r1) > 3.0) & (np.abs(r1) < 12)].max()  # rect-window sidelobes of the contiguous band
assert abs(GL_POS - gl) < 0.5, (GL_POS, gl)  # the lobe sits near c/(2 df); the sub-band envelope pulls it in a little
assert GL_LEVEL > PSL1 + 4  # clearly above the contiguous band's first sidelobe

# ---- Fig 4: velocity axis vs Ts and M ---------------------------------------------------------
ts = np.linspace(8e-3, 60e-3, 300)
ms = np.array([16, 32, 64, 128])
fig, axs = plt.subplots(1, 2, figsize=(6.4, 2.6))
axs[0].plot(ts * 1e3, LAM / (4 * ts), color=BLUE, lw=1.6)
for lab, tv, col, off in [("planned", TS_PLAN, ORANGE, (14, 8)), ("measured", TS_MEAS, GREEN, (-8, 40))]:
    axs[0].plot(tv * 1e3, LAM / (4 * tv), "o", color=col)
    axs[0].annotate(f"{lab} {tv * 1e3:.2f} ms:\n$\\pm${LAM / (4 * tv):.2f} m/s", (tv * 1e3, LAM / (4 * tv)),
                    textcoords="offset points", xytext=off, fontsize=7.5, color=col,
                    ha="left" if off[0] > 0 else "left")
axs[0].set_xlabel("sweep period $T_s$ (ms)")
axs[0].set_ylabel("$v_{amb}$ (m/s)")
axs[0].set_title("unambiguous velocity $\\pm v_{amb}=\\pm\\lambda/(4T_s)$", fontsize=9)
axs[0].set_ylim(0, 4.2)
for tv, lab, col in [(TS_PLAN, "planned", ORANGE), (TS_MEAS, "measured", GREEN)]:
    axs[1].plot(ms, 100 * LAM / (2 * ms * tv), "o-", color=col, label=lab)
for tv, col, dy in [(TS_PLAN, ORANGE, 0.55), (TS_MEAS, GREEN, 1.45)]:
    v = 100 * LAM / (2 * M * tv)
    axs[1].annotate(f"M=64: {v:.1f} cm/s", (M, v), textcoords="offset points", xytext=(10, 8) if col == GREEN else (-85, -6),
                    fontsize=7.5, color=col)
axs[1].set_xscale("log", base=2)
axs[1].set_xticks(ms)
axs[1].set_xticklabels([str(m) for m in ms])
axs[1].set_xlabel("sweeps per CPI $M$")
axs[1].set_ylabel("velocity resolution (cm/s)")
axs[1].set_title("resolution $=\\lambda/(2MT_s)$", fontsize=9)
axs[1].legend(frameon=False, fontsize=8)
fig.tight_layout()
savefig(fig, "fig_sfp_velocity.png")

# ---- Fig 5: time budget ----------------------------------------------------------------------
fig, axs = plt.subplots(1, 2, figsize=(6.6, 2.6), gridspec_kw={"width_ratios": [1.5, 1]})
ax = axs[0]
parts = [("retune", T.retune_s, ORANGE), ("wait", WAIT, GOLD), ("dwell", W.dwell_s, BLUE)]
left = 0.0
for name, d, col in parts:
    ax.barh(1, d * 1e3, left=left * 1e3, color=col, height=0.5)
    ax.text((left + d / 2) * 1e3, 1, (f"{name}\n{d * 1e3:.1f}" if d > 2e-3 else f"{d * 1e3:.0f}"), ha="center", va="center", fontsize=7.5)
    left += d
ax.barh(0, TSTEP_MEAS * 1e3, color=GREEN, height=0.5)
ax.text(TSTEP_MEAS * 1e3 / 2, 0, f"measured step {TSTEP_MEAS * 1e3:.2f} (RX-only run)", ha="center", va="center", fontsize=7.5)
ax.text(left * 1e3 / 2, 1.38, f"planned step = {STEP * 1e3:.1f} ms", ha="center", va="center", fontsize=8)
ax.set_yticks([0, 1])
ax.set_yticklabels(["measured", "planned"])
ax.set_xlabel("time within one step (ms)")
ax.set_xlim(0, 20)
ax.set_ylim(-0.4, 1.6)
ax.set_title("one step: retune, wait, dwell", fontsize=9)
ns = np.arange(1, 5)
ts_n = [compute_budget(with_waveform(num_steps=int(n))).sweep_time_s for n in ns]
vamb_n = [LAM / (4 * x) for x in ts_n]
assert math.isclose(ts_n[1], TS_PLAN)
axs[1].bar(ns, np.array(ts_n) * 1e3, color=BLUE, width=0.6)
for n, x, v in zip(ns, ts_n, vamb_n):
    axs[1].text(n, x * 1e3 + 1.5, f"{x * 1e3:.1f} ms\n$\\pm${v:.2f}", ha="center", fontsize=7)
axs[1].set_ylim(0, 100)
axs[1].set_xticks(ns)
axs[1].set_xlabel("steps $N$")
axs[1].set_ylabel("planned sweep $T_s$ (ms)")
axs[1].set_title("more steps, slower sweep", fontsize=9)
fig.tight_layout()
savefig(fig, "fig_sfp_timing.png")

# ---- numbers quoted --------------------------------------------------------------------------
chirp_samples = round(W.chirp_length_s * W.sample_rate_hz)
usb_nominal_msps = 61.44
usb_bytes = 4 * W.sample_rate_hz
print("numbers quoted (docs/theory/stepped_frequency_primer.md)")
print(f"  config                   configs/default.yaml; N={W.num_steps}, df={W.step_spacing_hz / 1e6:.0f} MHz, B={W.chirp_bandwidth_hz / 1e6:.0f} MHz, "
      f"fs={W.sample_rate_hz / 1e6:.0f} MS/s, Tc={W.chirp_length_s * 1e6:.0f} us ({chirp_samples} samples), dwell={W.dwell_s * 1e3:.0f} ms ({N_CHIRPS} chirps)")
print(f"  chirp slope B/Tc         {SLOPE / 1e12:.2f} THz/s; beat 2*slope/c = {2 * SLOPE / C / 1e3:.1f} kHz per metre of range")
assert round(10 * math.log10(N_CHIRPS), 1) == 17.0 and chirp_samples == 1120 and abs(CFG.rf.center_freq_hz - 2441.75e6) < 1
print(f"  averaging gain           10 log10({N_CHIRPS}) = {10 * math.log10(N_CHIRPS):.1f} dB; N_c = Tc*fs = {chirp_samples}; centre frequency {CFG.rf.center_freq_hz / 1e6:.2f} MHz (lambda = c/f_c)")
assert T.command_lead_s == 0.002 and T.settle_margin_s == 0.008 and abs(WAIT - 0.008) < 1e-12
print(f"  wait                     max(settle {T.settle_margin_s * 1e3:.1f}, command_lead {T.command_lead_s * 1e3:.1f} + Tc {W.chirp_length_s * 1e3:.2f} = {(T.command_lead_s + W.chirp_length_s) * 1e3:.2f}) = {WAIT * 1e3:.1f} ms")
assert abs(np.mean([CFG.rf.center_freq_hz - W.step_spacing_hz / 2, CFG.rf.center_freq_hz + W.step_spacing_hz / 2]) - CFG.rf.center_freq_hz) < 1
print(f"  step centres             {(CFG.rf.center_freq_hz - W.step_spacing_hz / 2) / 1e6:.2f}, {(CFG.rf.center_freq_hz + W.step_spacing_hz / 2) / 1e6:.2f} MHz")
print(f"  R_amb exact c            {DD_PLAN.max_unambiguous_range_m:.1f} m")
print(f"  Doppler at v_amb         f_D = 2 v_amb/lambda = {2 * DD_MEAS.velocity_span_mps / LAM:.1f} Hz = 1/(2 Ts) = {DD_MEAS.doppler_span_hz:.1f} Hz")
print(f"  B_tot                    {B_TOT / 1e6:.0f} MHz   (one step alone: {C / (2 * W.chirp_bandwidth_hz):.2f} m)")
print(f"  range resolution         {DD_PLAN.range_resolution_m:.2f} m")
print(f"  R_amb = c Tc/2           {DD_PLAN.max_unambiguous_range_m:.0f} m")
print(f"  grating lobe, df={spacing_gap / 1e6:.0f} MHz   lobe near c/(2df) = {gl:.2f} m (peak at {GL_POS:.2f} m), {GL_LEVEL:.1f} dB (contiguous band: first sidelobe {PSL1:.1f} dB); rect window, illustrative")
print(f"  df/B                     {W.step_spacing_hz / W.chirp_bandwidth_hz:.2f}")
print(f"  df/B warn threshold      0.9 B = {0.9 * W.chirp_bandwidth_hz / 1e6:.1f} MHz (docs/delay_doppler.md A.2)")
print(f"  lambda                   {LAM * 100:.2f} cm")
print(f"  step planned             retune {T.retune_s * 1e3:.1f} + wait {WAIT * 1e3:.1f} + dwell {W.dwell_s * 1e3:.1f} = {STEP * 1e3:.1f} ms")
print(f"  sweep planned            {TS_PLAN * 1e3:.2f} ms -> v_amb +/-{DD_PLAN.velocity_span_mps:.2f} m/s, dv {DD_PLAN.velocity_resolution_mps * 100:.1f} cm/s at M={M}")
print(f"  sweep measured           {TS_MEAS * 1e3:.3f} ms ({MEAS_SRC}); step {TSTEP_MEAS * 1e3:.2f} ms")
print(f"                           -> v_amb +/-{DD_MEAS.velocity_span_mps:.2f} m/s, dv {DD_MEAS.velocity_resolution_mps * 100:.1f} cm/s at M={M}, "
      f"Doppler span +/-{DD_MEAS.doppler_span_hz:.1f} Hz, resolution {DD_MEAS.doppler_resolution_hz:.2f} Hz")
if (RUN / "bursts.csv").exists():
    others = {d.name: measured_sweep_period(d)["sweep_period_s"] for d in sorted(RUN.parent.glob("run_*_rx")) if d != RUN and (d / "bursts.csv").exists()}
    print(f"  measured: sweeps         {MEAS['sweeps']} sweeps, sigma {MEAS['sweep_period_std_s'] * 1e3:.2f} ms; other RX-only runs: "
          + ", ".join(f"{v * 1e3:.2f}" for v in others.values()) + " ms")
print(f"  measured - planned       {(TS_MEAS - TS_PLAN) * 1e3:+.2f} ms per sweep, {(TSTEP_MEAS - STEP) * 1e3:+.2f} ms per step")
print(f"  step if retune were      {(TSTEP_MEAS - WAIT - W.dwell_s) * 1e3:.2f} ms (measured step minus wait minus dwell, wait/dwell as configured)")
print(f"  data per step / USB      {usb_bytes / 1e6:.0f} MB/s sc16 at {W.sample_rate_hz / 1e6:.0f} MS/s vs nominal ceiling {usb_nominal_msps} MS/s ({4 * usb_nominal_msps:.2f} MB/s), configs/hw_limits.yaml")
print(f"  duty cycle (TX)          {BUD.tx_duty_cycle * 100:.1f} %")
print("  sweep vs N (planned)     " + ", ".join(f"N={n}: {x * 1e3:.1f} ms, +/-{v:.2f} m/s" for n, x, v in zip(ns, ts_n, vamb_n)))
print("figures: " + ", ".join(sorted(p.name for p in OUT.glob("fig_sfp_*.png"))))
