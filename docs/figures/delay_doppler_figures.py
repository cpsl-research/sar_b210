"""Figures and numbers for docs/delay_doppler.md (b210-13 Step 1).

Noise-free numpy model of the default config (2 steps, 30 MHz spacing, 45 MHz chirps,
56 MS/s, 1120-sample chirp, 5 % taper). It is a check of the memo's formulas, not the Step 3
implementation, but it follows its choices (-6 dB keep, midpoint split, 1/|X|^2, DC bin replaced
by the mean of its equalized neighbours, one Hann window over the stitched support). Run with any python that has numpy + matplotlib (the sar_b210 venv has no
matplotlib on purpose):

    python3 docs/figures/delay_doppler_figures.py

Writes fig_stitch.png (processing chain, sub-bands, profiles) and fig_motion.png next to this
file, prints the numbers the memo quotes (mainlobe width, PSL, peak loss), and checks that no
annotation overlaps a plotted curve or leaves its box.
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


def _dc_fill(val):
    """Equalized DC-bin value: mean of the equalized bins -1 and +1 (Step 3 default dc="interpolate")."""
    return 0.5 * (val[KB == -1][0] + val[KB == 1][0])


def stitch(zs, window="hann", split=True, equalize=True, dc="interpolate"):
    """Frequency-domain concatenation on the 50 kHz grid; returns (grid bins g, spectrum)."""
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
        if dc == "interpolate":
            s[step_bins[k] - lo] += _dc_fill(val)
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
    val = z / x2
    s = np.where(keep, val, 0)
    s[KB == 0] = _dc_fill(val)
    s = s[order]
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


CHAIN = [  # (row, col, text): the Step 3 processing chain, per burst (row 0) and per CPI (row 1)
    (0, 0, "burst $(m,k)$: IQ,\n50 chirps x 1120"),
    (0, 1, "average the\n50 chirps (+17 dB)"),
    (0, 2, "FFT, multiply by $X^*(f)$\n$\\to Y_{m,k}(f)$"),
    (0, 3, "divide by $c_{m,k}$;\nshift by $\\tau_{env}$, $\\tau_L$\n$\\to Z_{m,k}(f)$"),
    (1, 0, "stack $M$ sweeps:\n$Z$ is $M\\times N\\times 1120$"),
    (1, 1, "Doppler DFT over $m$\nper step, at $t_{m,k}$\n$\\to D_k(f_D,f)$"),
    (1, 2, "stitch each $f_D$ row:\nsplit, $1/|X|^2$,\nDC fill, Hann"),
    (1, 3, "zero-padded IFFT\n$\\to$ map($f_D$, $R$)"),
]
BOX_W, BOX_H, GAP = 0.215, 0.40, 0.045


def _box_xy(row, col):
    return 0.0 + col * (BOX_W + GAP), 0.55 - row * 0.55   # lower-left corner (axes coords)


def draw_chain(ax):
    """Panel (a): block diagram of the processing chain."""
    from matplotlib.patches import FancyBboxPatch
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    for row, col, text in CHAIN:
        x, y = _box_xy(row, col)
        face = "#eaf2fb" if row == 0 else "#fdf0e9"
        ax.add_patch(FancyBboxPatch((x, y), BOX_W, BOX_H, boxstyle="round,pad=0.005,rounding_size=0.02",
                                    fc=face, ec=COLORS[row], lw=1.2))
        ax.text(x + BOX_W / 2, y + BOX_H / 2, text, ha="center", va="center", fontsize=7.5, color=INK)
        if col < 3:
            ax.annotate("", xy=(x + BOX_W + GAP, y + BOX_H / 2), xytext=(x + BOX_W, y + BOX_H / 2),
                        arrowprops=dict(arrowstyle="-|>", color=INK, lw=1))
    x3, y3 = _box_xy(0, 3)
    x0, y0 = _box_xy(1, 0)
    ymid = (y3 + y0 + BOX_H) / 2
    ax.plot([x3 + BOX_W / 2, x3 + BOX_W / 2, x0 + BOX_W / 2], [y3, ymid, ymid], color=INK, lw=1)
    ax.annotate("", xy=(x0 + BOX_W / 2, y0 + BOX_H), xytext=(x0 + BOX_W / 2, ymid),
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1))


def check_layout(fig, name):
    """Fail loudly if any text (incl. legends) overlaps a plotted curve, or a chain label leaves its box."""
    from matplotlib.legend import Legend
    from matplotlib.patches import FancyBboxPatch
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    for ax in fig.axes:
        pts = []
        ab = ax.get_window_extent(r)
        for ln in ax.get_lines():
            if ln.get_gid() == "ref":               # reference lines (axvline/axhline) may sit under labels
                continue
            xy = ax.transData.transform(np.column_stack(ln.get_data()))
            if len(xy) > 1:                       # densify so a box between two vertices is caught
                xy = np.concatenate([np.linspace(a, b, 20) for a, b in zip(xy[:-1], xy[1:])])
            pts.append(xy)
        pts = np.concatenate(pts) if pts else np.zeros((0, 2))
        pts = pts[(pts[:, 0] >= ab.x0) & (pts[:, 0] <= ab.x1) & (pts[:, 1] >= ab.y0) & (pts[:, 1] <= ab.y1)]
        boxes = [p.get_window_extent(r) for p in ax.patches if isinstance(p, FancyBboxPatch)]
        arts = list(ax.texts) + [c for c in ax.get_children() if isinstance(c, Legend)]
        for t in arts:
            bb = t.get_window_extent(r)
            hit = (pts[:, 0] > bb.x0) & (pts[:, 0] < bb.x1) & (pts[:, 1] > bb.y0) & (pts[:, 1] < bb.y1)
            if hit.any() and ax.axison:
                raise SystemExit(f"{name}: '{getattr(t, 'get_text', lambda: 'legend')()}' overlaps a curve")
            if boxes and t.get_text().count("\n") >= 1:
                if not any(b.x0 <= bb.x0 and bb.x1 <= b.x1 and b.y0 <= bb.y0 and bb.y1 <= b.y1 for b in boxes):
                    raise SystemExit(f"{name}: chain label '{t.get_text()[:30]}' leaves its box")
    labels = []                                    # titles, axis labels, texts, legends must not collide
    for ax in fig.axes + [c for ax in fig.axes for c in getattr(ax, "child_axes", [])]:
        labels += [t for t in (ax.title, ax.xaxis.label, ax.yaxis.label, *ax.texts) if t.get_text()]
        labels += [c for c in ax.get_children() if isinstance(c, Legend)]
    ext = [t.get_window_extent(r) for t in labels]
    for i in range(len(ext)):
        for j in range(i + 1, len(ext)):
            if ext[i].overlaps(ext[j]):
                raise SystemExit(f"{name}: labels {i} and {j} overlap")
    print(f"{name}: layout check passed")


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

    fig, (ax0, ax1, ax2) = plt.subplots(3, 1, figsize=(7.2, 9.8), gridspec_kw={"height_ratios": [1.05, 1, 1.6]})
    draw_chain(ax0)
    ax0.set_title("(a) Processing chain (Step 3 code): blue per burst, orange per CPI of $M$ sweeps", fontsize=10, loc="left")
    x2 = np.abs(X) ** 2
    o = np.argsort(F)
    g_k = OFFS / DELTA_F                                   # grid bin of each step centre (-300, +300)
    for k in range(2):
        ax1.plot(KB[o] + g_k[k], 10 * np.log10(x2[o] / x2.max()), color=COLORS[k], lw=2,
                 label=f"step {k}: $|X(f)|^2$, centre $f_k$ at grid bin {g_k[k]:+.0f}")
        ax1.axvline(g_k[k], color=COLORS[k], lw=1, ls=":", gid="ref",
                    label=f"step {k} centre = its DC bin (filled from neighbours)")
    ax1.axvspan(-150, 150, color=MUTED, alpha=0.15, lw=0)
    ax1.axvline(0, color=INK, lw=1, ls="--", gid="ref", label="overlap split (bin 0)")
    ax1.axhline(-6, color=MUTED, lw=1, ls="-.", gid="ref", label="-6 dB keep level")
    ax1.text(0, -18, "15 MHz\noverlap", fontsize=8, color=INK, ha="center", va="center",
             bbox=dict(fc="white", ec="none", pad=1))
    ax1.set_xlim(-880, 880)
    ax1.set_ylim(-30, 3)
    ax1.set_xlabel(r"stitched grid bin $g = n + (f_k - f_c)/\delta f$  ($\delta f$ = 50 kHz)")
    ax1.set_ylabel("dB")
    top = ax1.secondary_xaxis("top", functions=(lambda g: g * DELTA_F / 1e6, lambda mhz: mhz * 1e6 / DELTA_F))
    top.set_xlabel(r"$\nu - f_c$ (MHz)", fontsize=8)
    top.tick_params(labelsize=8)
    ax1.set_title("(b) Two 45 MHz sub-bands on the common 50 kHz grid", fontsize=10, loc="left", pad=22)
    ax1.legend(fontsize=7.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.33), ncol=2)
    show = ["stitched, Hann (recommended)", "single 45 MHz step, Hann", "stitched, envelope ref. rounded to 159 samples", "LO phase not removed"]
    for i, name in enumerate(show):
        r, p = cases[name]
        ax2.plot(r, 20 * np.log10(p / ref_peak + 1e-12), color=COLORS[i], lw=2 if i == 0 else 1.5, label=name)
    ax2.set_xlim(r0 - 14, r0 + 14)
    ax2.set_ylim(-60, 3)
    ax2.set_xlabel("range (m, half path)")
    ax2.set_ylabel("dB re. stitched peak")
    ax2.set_title(f"(c) Point target at {r0:.0f} m, random LO phase per step, noise-free", fontsize=10, loc="left")
    ax2.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2)
    for ax in (ax1, ax2):
        ax.grid(color="#e6e5e0", lw=0.6)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    check_layout(fig, "fig_stitch")
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
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    ax.plot(speeds, loss_sf, color=COLORS[1], lw=2, label="stitch-first: stitch each sweep, FFT over sweeps")
    ax.plot(speeds, loss_df, color=COLORS[0], lw=2, label="Doppler-first: per-step DFT at burst times, then stitch")
    ax.axvline(v_amb, color=INK, lw=1, ls="--")
    ax.text(v_amb, 1.5, f"$v_{{amb}}=\\lambda/(4\\bar T_s)$ = {v_amb:.2f} m/s (Doppler aliases beyond)",
            fontsize=8, color=INK, ha="center", va="bottom", clip_on=False)
    ax.set_xlabel("closing speed (m/s)")
    ax.set_ylabel("loss (dB)")
    ax.set_ylim(-50, 1)
    ax.set_xlim(0, 2)
    ax.set_title(f"Moving point target: loss at its mid-CPI range (M = {m_sw}, $T_{{step}}$ = 15.3 ms)",
                 fontsize=10, loc="left", pad=16)
    ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=1)
    ax.grid(color="#e6e5e0", lw=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    check_layout(fig, "fig_motion")
    fig.savefig(OUT / "fig_motion.png", dpi=130)


if __name__ == "__main__":
    gen = np.random.default_rng(13)
    fig_stitch(gen)
    fig_motion(gen)
