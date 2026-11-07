"""Figures and numbers for docs/delay_doppler.md (b210-13 Step 1).

Noise-free numpy model of the default config (2 steps, 30 MHz spacing, 45 MHz chirps,
56 MS/s, 1120-sample chirp, 5 % taper). It is a check of the memo's formulas, not the Step 3
implementation. Run with any python that has numpy + matplotlib (the sar_b210 venv has no
matplotlib on purpose):

    python3 docs/figures/delay_doppler_figures.py

Writes fig_stitch.png and fig_motion.png next to this file and prints the numbers the memo
quotes (mainlobe width, PSL, peak loss).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

C = 299792458.0
FS = 56e6
NC = 1120                      # chirp samples = circular period
B = 45e6                       # chirp bandwidth
DF = 30e6                      # step spacing
FC = 2441.75e6
TAPER = 0.05
DELTA_F = FS / NC              # 50 kHz grid
OFFS = np.array([-0.5, 0.5]) * DF  # f_k - f_c for 2 steps
T_SYS = 159.3 / FS             # baseband latency (envelope only), fractional on purpose
TAU_L = 0.3 / C                # leakage RF path, 0.3 m antenna separation
OUT = Path(__file__).resolve().parent
INK, MUTED = "#2b2b2b", "#8a8a85"
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]


def chirp() -> np.ndarray:
    """Same formula as capture/src/chirp.cpp, unquantized and at unit amplitude."""
    n = np.arange(NC)
    t = n / FS
    dur = NC / FS
    ph = 2 * np.pi * (-B / 2 * t + B / (2 * dur) * t * t)
    taper_len = int(round(TAPER * NC))
    edge = np.minimum(n, NC - 1 - n)
    w = np.where(edge < taper_len, 0.5 * (1 - np.cos(np.pi * (edge + 0.5) / taper_len)), 1.0)
    return w * np.exp(1j * ph)


X = np.fft.fft(chirp())
F = np.fft.fftfreq(NC, 1 / FS)          # baseband bin frequencies
KB = np.rint(F / DELTA_F).astype(int)   # baseband bin index, -560..559


def received(targets, phi, tau_sys=T_SYS):
    """Per-step matched-filter spectra Y_k(f) = R_k(f) X*(f); targets = [(amp, tau_rf), ...]."""
    ys = []
    for k, off in enumerate(OFFS):
        g = sum(a * np.exp(-2j * np.pi * (FC + off + F) * tau) for a, tau in targets)
        r = np.exp(1j * phi[k]) * X * np.exp(-2j * np.pi * F * tau_sys) * g
        ys.append(r * np.conj(X))
    return ys


def normalize(ys, ys_leak, tau_env, tau_l=TAU_L):
    """Per-step LO-phase removal: divide by the leakage peak value, align envelopes, apply tau_L.

    c_k is read from the leakage component only, so the plotted profiles show the target lobe
    without the leakage lobe at 0 m (in real data c_k is the full profile at the leakage peak).
    """
    out = []
    for k, (y, yl) in enumerate(zip(ys, ys_leak)):
        c_k = np.sum(yl * np.exp(2j * np.pi * F * tau_env)) / NC     # profile value at the leakage peak
        z = y / c_k * np.exp(2j * np.pi * F * tau_env)               # leakage now at zero delay
        out.append(z * np.exp(-2j * np.pi * (OFFS[k] + F) * tau_l))  # absolute RF delay
    return out


def stitch(zs, window="hann", split=True, equalize=True):
    """Frequency-domain concatenation on the 50 kHz grid; returns (nu_bins, spectrum)."""
    step_bins = np.rint(OFFS / DELTA_F).astype(int)
    lo, hi = step_bins[0] + KB.min(), step_bins[-1] + KB.max()
    nu = np.arange(lo, hi + 1)
    s = np.zeros(nu.size, complex)
    x2 = np.abs(X) ** 2
    mid = (step_bins[0] + step_bins[1]) / 2
    for k, z in enumerate(zs):
        nu_idx = KB + step_bins[k]
        keep = x2 > 0.25 * x2.max()                  # flat part of the sub-band (drops taper skirts)
        keep &= KB != 0                              # DC bin of each step
        if split:                                    # midpoint split of the overlap
            keep &= (nu_idx < mid) if k == 0 else (nu_idx >= mid)
        val = z / x2 if equalize else z / x2.max()
        np.add.at(s, nu_idx[keep] - lo, val[keep])
    support = np.abs(s) > 0
    i0, i1 = np.flatnonzero(support)[[0, -1]]
    w = np.zeros(nu.size)
    n_sup = i1 - i0 + 1
    w[i0 : i1 + 1] = np.hanning(n_sup) if window == "hann" else np.ones(n_sup)
    return nu, s * w


def single_step(z, window="hann"):
    x2 = np.abs(X) ** 2
    keep = (x2 > 0.25 * x2.max()) & (KB != 0)
    order = np.argsort(KB)
    nu = KB[order]
    s = np.where(keep, z / x2, 0)[order]
    i0, i1 = np.flatnonzero(np.abs(s) > 0)[[0, -1]]
    w = np.zeros(nu.size)
    w[i0 : i1 + 1] = np.hanning(i1 - i0 + 1) if window == "hann" else 1
    return nu, s * w


def profile(nu, s, pad=64):
    """Range profile in metres (half path) by zero-padded inverse DFT on the 50 kHz grid."""
    n = 2 ** int(np.ceil(np.log2(nu.size * pad)))
    full = np.zeros(n, complex)
    full[np.mod(nu, n)] = s
    p = np.fft.ifft(full) * n
    r = np.arange(n) * C / (2 * n * DELTA_F)
    r = np.where(r > C / (4 * DELTA_F), r - C / (2 * DELTA_F), r)
    o = np.argsort(r)
    return r[o], np.abs(p[o])


def metrics(r, p, r0):
    """-3 dB width and peak sidelobe (dB) of the lobe nearest r0."""
    i = np.argmin(np.abs(r - r0))
    i = i - 50 + np.argmax(p[i - 50 : i + 50])
    db = 20 * np.log10(p / p[i] + 1e-12)
    a = i
    while db[a] > -3:
        a -= 1
    b = i
    while db[b] > -3:
        b += 1
    lo = i
    while p[lo - 1] < p[lo]:
        lo -= 1
    hi = i
    while p[hi + 1] < p[hi]:
        hi += 1
    win = (np.abs(r - r[i]) < 60) & ((np.arange(r.size) < lo) | (np.arange(r.size) > hi))
    return r[b] - r[a], db[win].max(), r[i], p[i]


def fig_stitch(rng):
    r0 = 40.0
    tgt = [(1.0, 2 * r0 / C)]
    phi = np.array([0.3, 0.3 + np.deg2rad(100)])   # ~100 deg: the measured burst-to-burst spread
    leak = [(1.0, TAU_L)]
    ys_l = received(leak, phi)
    ys_t = received(tgt, phi)
    tau_env = T_SYS + TAU_L
    cases = {}
    zs = normalize(ys_t, ys_l, tau_env)
    cases["stitched, Hann (recommended)"] = profile(*stitch(zs))
    cases["single 45 MHz step, Hann"] = profile(*single_step(zs[0]))
    cases["stitched, envelope ref. rounded to 159 samples"] = profile(*stitch(normalize(ys_t, ys_l, round(T_SYS * FS) / FS + TAU_L)))
    cases["overlap summed, not equalized"] = profile(*stitch(zs, split=False, equalize=False))
    zs_raw = [z * np.exp(1j * phi[k]) for k, z in enumerate(zs)]  # put the LO phase back
    cases["LO phase not removed"] = profile(*stitch(zs_raw))

    results = {}
    for name, (r, p) in cases.items():
        results[name] = metrics(r, p, r0)
    ref_peak = results["stitched, Hann (recommended)"][3]
    print("case | -3 dB width m | PSL dB | peak m | peak loss dB")
    for name, (w, psl, rp, pk) in results.items():
        print(f"{name} | {w:.2f} | {psl:.1f} | {rp:.2f} | {20*np.log10(pk/ref_peak):.2f}")
    r_rect, p_rect = profile(*stitch(zs, window="rect"))
    w, psl, *_ = metrics(r_rect, p_rect, r0)
    print(f"stitched, rectangular | {w:.2f} | {psl:.1f}")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7.2, 6.4), gridspec_kw={"height_ratios": [1, 1.6]})
    x2 = np.abs(X) ** 2
    o = np.argsort(F)
    for k in range(2):
        ax1.plot((OFFS[k] + F[o]) / 1e6, 10 * np.log10(x2[o] / x2.max()), color=COLORS[k], lw=2, label=f"step {k}: $|X(f)|^2$ at $f_c{OFFS[k]/1e6:+.0f}$ MHz")
        ax1.axvline(OFFS[k] / 1e6, color=COLORS[k], lw=1, ls=":")
    ax1.axvspan(-7.5, 7.5, color=MUTED, alpha=0.15, lw=0)
    ax1.axvline(0, color=INK, lw=1, ls="--")
    ax1.text(0.4, -17, "overlap 15 MHz\nsplit at midpoint", fontsize=8, color=INK)
    ax1.text(16, -9, "dotted: step centres =\nDC of each step (zeroed)", fontsize=8, color=INK)
    ax1.set_xlim(-42, 42)
    ax1.set_ylim(-30, 3)
    ax1.set_xlabel(r"RF offset $\nu - f_c$ (MHz)")
    ax1.set_ylabel("dB")
    ax1.set_title("(a) Two 45 MHz sub-bands on the common 50 kHz grid", fontsize=10, loc="left")
    ax1.legend(fontsize=8, loc="lower left", frameon=False)
    show = ["stitched, Hann (recommended)", "single 45 MHz step, Hann", "stitched, envelope ref. rounded to 159 samples", "LO phase not removed"]
    for i, name in enumerate(show):
        r, p = cases[name]
        ax2.plot(r, 20 * np.log10(p / ref_peak + 1e-12), color=COLORS[i], lw=2 if i == 0 else 1.5, label=name)
    ax2.set_xlim(r0 - 14, r0 + 14)
    ax2.set_ylim(-60, 3)
    ax2.set_xlabel("range (m, half path)")
    ax2.set_ylabel("dB re. stitched peak")
    ax2.set_title(f"(b) Point target at {r0:.0f} m, random LO phase per step, noise-free", fontsize=10, loc="left")
    ax2.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
    for ax in (ax1, ax2):
        ax.grid(color="#e6e5e0", lw=0.6)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "fig_stitch.png", dpi=130)


def fig_motion(rng):
    """Loss at the true range vs radial speed: stitch-then-FFT vs per-step Doppler with timestamp compensation."""
    m_sw = 64
    t_step = 15.32e-3                    # measured run_20261106_155516_rx
    t_sweep = 2 * t_step
    jitter = 0.34e-3
    t = np.arange(m_sw)[:, None] * t_sweep + np.arange(2)[None, :] * t_step + rng.normal(0, jitter, (m_sw, 2))
    lam = C / FC
    wm = np.hanning(m_sw)
    r0 = 40.0
    speeds = np.linspace(0, 2.0, 81)
    t_mid = t.mean()
    loss_sf, loss_df = [], []

    def at(nu, s, r):
        rr, p = profile(nu, s, pad=16)
        return p[np.argmin(np.abs(rr - r))]               # value at the target's mid-CPI range

    def bursts(v):
        """Every burst simulated at its own range r0 - v t, its own random LO phase, leakage-normalized."""
        out = []
        for m in range(m_sw):
            phi = rng.uniform(-np.pi, np.pi, 2)
            ys = [received([(1.0, 2 * (r0 - v * t[m, k]) / C)], phi)[k] for k in range(2)]
            out.append(normalize(ys, received([(1.0, TAU_L)], phi), T_SYS + TAU_L))
        return out

    nu, s_static = stitch(bursts(0.0)[0])
    ref = at(nu, s_static * wm.sum(), r0)
    for v in speeds:
        fd = 2 * v / lam                                  # closing speed -> positive Doppler
        fd_seen = (fd + 0.5 / t_sweep) % (1 / t_sweep) - 0.5 / t_sweep   # bin it lands in (aliases > v_amb)
        zs = bursts(v)
        r_mid = r0 - v * t_mid
        # stitch-first: stitch every sweep, then DFT over sweeps at the sweep (step-0) times
        acc = sum(wm[m] * stitch(zs[m])[1] * np.exp(-2j * np.pi * fd_seen * (t[m, 0] - t_mid)) for m in range(m_sw))
        loss_sf.append(20 * np.log10(at(nu, acc, r_mid) / ref))
        # Doppler-first: per-step DFT at each burst's own time, then stitch per Doppler bin
        dk = [sum(wm[m] * zs[m][k] * np.exp(-2j * np.pi * fd_seen * (t[m, k] - t_mid)) for m in range(m_sw)) for k in range(2)]
        loss_df.append(20 * np.log10(at(*stitch(dk), r_mid) / ref))
    loss_sf, loss_df = np.array(loss_sf), np.array(loss_df)
    for v in (0.25, 0.5, 0.75, 1.0, 1.5):
        i = np.argmin(np.abs(speeds - v))
        print(f"v={v:.2f} m/s: stitch-first {loss_sf[i]:.2f} dB, doppler-first {loss_df[i]:.2f} dB")

    v_amb = lam / (4 * t_sweep)
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    ax.plot(speeds, loss_sf, color=COLORS[1], lw=2, label="stitch-first: stitch each sweep, FFT over sweeps")
    ax.plot(speeds, loss_df, color=COLORS[0], lw=2, label="Doppler-first: per-step DFT at burst times, then stitch")
    ax.axvline(v_amb, color=INK, lw=1, ls="--")
    ax.text(v_amb + 0.02, -30, f"$\\lambda/(4T_s)$ = {v_amb:.2f} m/s\n(Doppler aliases beyond)", fontsize=8, color=INK)
    ax.set_xlabel("closing speed (m/s)")
    ax.set_ylabel("loss (dB)")
    ax.set_ylim(-50, 1)
    ax.set_xlim(0, 2)
    ax.set_title(f"Moving point target: loss at its mid-CPI range (M = {m_sw}, $T_{{step}}$ = 15.3 ms)", fontsize=10, loc="left")
    ax.legend(fontsize=8, frameon=False, loc="center left", bbox_to_anchor=(0.0, 0.42))
    ax.grid(color="#e6e5e0", lw=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "fig_motion.png", dpi=130)


if __name__ == "__main__":
    gen = np.random.default_rng(13)
    fig_stitch(gen)
    fig_motion(gen)
