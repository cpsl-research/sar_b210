# Delay-Doppler from stitched stepped chirps: method

Method for `b210_sar.processing.delay_doppler` (directive b210-13, Step 1). 2026-11-06.
Parameters come from `configs/default.yaml`, `capture/src/*.cpp` and the RX-only run
`run_20261106_155516_rx` (1959 bursts, default config). Simulated numbers and figures come from
`docs/figures/delay_doppler_figures.py`, a noise-free numpy model. Run it with `python3`; the venv
has no matplotlib.

## TL;DR

- For each burst: matched-filter against `chirp_replica.cf32`, then divide by the leakage peak to
  remove the random LO phase. Then **align each step's envelope to the leakage with sub-sample
  accuracy.** Rounding the alignment to whole samples still looks plausible, but sidelobes rise from
  -32 to -13 dB (Fig. 1b).
- Stitch in the frequency domain on the 50 kHz grid. Split the overlap at its midpoint, equalize
  by $1/|X|^2$, and apply one Hann window. Result: 3.0 m -3 dB width and -32 dB PSL, against 5.1 m
  for one step.
- For the map, **compute Doppler first** (a per-step DFT at the burst timestamps), then stitch.
  Stitching first costs a moving target $\cos(\pi f_D T_{step})$, which is -3 dB at 1 m/s (Fig. 2).
- The measured sweep is **30.64 ms**, not the budget's 36.40 ms. So $v_{amb}$ is **±1.00 m/s**,
  not ±0.84 m/s.

## 1. Parameters (verified)

| Quantity | Value | Source |
|---|---|---|
| $N$, $\Delta f$, $B$ | 2, 30 MHz, 45 MHz; $f_k$ = 2426.75, 2456.75 MHz | config, bursts.csv |
| $f_s$, $T_c$, $N_c$ | 56 MS/s, 20 µs, 1120 samples; up-chirp $-B/2\to+B/2$, 5 % taper | `chirp.cpp` |
| Burst | 50 chirps (56000 samples); TX sends 51 and starts one chirp early | `make_tx_burst` |
| $T_{step}$ / $T_s$ | 15.32 / **30.64 ms** mean (σ 0.34, max 32.82 ms); budget 18.2 / 36.40 ms | bursts.csv, `run_log.txt` |
| $\lambda$; leakage delay $\tau_{env}$ | 12.28 cm; ≈ 159 samples (2.84 µs, cabled bench) | config, `design_notes.md` |

## 2. Signal model

The 50 chirps of burst $m$ (step $k$, time $t_{m,k}$) are averaged, for 17 dB of gain. The result
is transformed on $f=n\,\delta f$, $\delta f=f_s/N_c$ = 50 kHz, and multiplied by $X^*(f)$:

$$Y_{m,k}(f) = |X(f)|^2\, e^{j\phi_{m,k}}\, e^{-j2\pi f\tau_{sys}} \sum_i a_i\, e^{-j2\pi (f_k+f)\,\tau_i(t_{m,k})}.$$

- $\tau_i$ is the bistatic path divided by $c$, so $R=c\tau/2$. A closing speed $v$ gives
  $\tau_i(t)=\tau_{i0}-2vt/c$ and $f_D=2vf_k/c$.
- $\tau_{sys}$ is baseband latency, which shifts the envelope only.
- $\phi_{m,k}$ is the TX-RX synthesizer phase. It is random after every retune: consecutive bursts
  spread by about 100°.
- The leakage is the term with delay $\tau_L$ (antenna separation divided by $c$).

Matched filtering runs at full rate rather than by dechirp, so there is no residual video phase.
LFM range-Doppler coupling (7 ps) and Doppler within a dwell (< 0.01 dB) are negligible. Circular
compression is exact for $\tau+\tau_{sys}\le T_c$, because the TX lead keeps the RX window in
steady state.

## 3. Removing the per-step LO phase

$\phi_{m,k}$ changes on every retune, so no stored calibration can predict it. Every burst needs
an in-burst reference: the leakage, or the outline's cabled reference on channel B.

$$Z_{m,k}(f) = \frac{Y_{m,k}(f)}{c_{m,k}}\, e^{+j2\pi f\tau_{env}}\, e^{-j2\pi(f_k-f_c+f)\tau_L},
\qquad c_{m,k}=y_{m,k}(\tau_{env}),\ \ \tau_{env}=\tau_{sys}+\tau_L .$$

$c_{m,k}$ is the compressed pulse at the leakage peak. Dividing by it leaves
$Z\propto|X|^2\sum_i(a_i/a_L)e^{-j2\pi(f_k+f)\tau_i}$, which is consistent across steps.

- **The envelope reference must be fractional.** An error $\delta$ creates a phase jump
  $2\pi\Delta f\delta$ at the junction. Simulated PSL is -29 dB at 0.02 samples, -25.7 dB at 0.05,
  and -13.4 dB at 0.3 samples, which is what rounding to 159 gives. The peak barely moves. Fit
  $\tau_{env}$ once per run, from the band phase slope of the averaged, normalized leakage.
- **Sign.** Under this model the $\tau_L$ correction is $e^{-j2\pi\nu\tau_L}$. The model follows
  the outline's backprojection kernel $e^{+j2\pi fR/c}$. The outline (p. 6) and the `apply_tau_leak`
  docstring write $e^{+j2\pi f_k\tau_{leak}}$. When the correction is applied over the full
  $\nu=f_k+f$, a wrong sign only shifts the range axis by $c\tau_L$; test 4 pins the sign.

`calibration.py` holds only static terms: the leakage window, $\tau_{env}$, $\tau_L$ (from the
antenna separation or a corner reflector), and `phase_only`. $c_{m,k}$ is computed per burst. For
RX-only runs, which have no leakage, set $c=1$.

## 4. Stitching

![Fig. 1](figures/fig_stitch.png)

*Fig. 1. (a) Each step's $|X|^2$ at its RF offset; the overlap is split at its midpoint.
(b) Point target at 40 m with a 100° LO-phase difference between the steps.*

Use **frequency-domain concatenation** [`wilkinson1998`; `lord1997`; `nel2002`]. Time-domain
synthesis reconstructs the same band, but it must upsample and frequency-shift every pulse, which
Lord [`lord2000`] found inefficient. $Z_{m,k}$ goes to bins $n+(f_k-f_c)/\delta f$ (±300), which
needs $\Delta f/\delta f$ to be an integer (here 600). Tuned frequencies are within 8 mHz of
nominal.

**Overlap.** The 15 MHz overlap is split at its midpoint, so each bin comes from one step. Adding
the overlap causes ripple [`yuan2009`]. Here, summing without equalizing gives 3.48 m and -25.9 dB.
The kept bins are those where $|X|^2>-6$ dB, which drops the taper skirts, equalized by $1/|X|^2$.
Each step's DC bin is zeroed, because its phase is random after normalization and it would smear
across Doppler. Then one Hann window is applied (numpy has no Taylor) and the IFFT is zero-padded
at least 4×. As a free per-burst calibration check, the phase residual between the two steps in
the overlap can be monitored. That is our proposal, not from the literature.

## 5. Resolution, sidelobes, ambiguities (formulas for b210-11)

| Quantity | Formula | Default |
|---|---|---|
| Band, resolution | $B_{tot}=(N-1)\Delta f+B$; $\Delta R=c/(2B_{tot})$; one step $c/(2B)$ | 75 MHz; 2.00 m; 3.33 m |
| -3 dB width / PSL (sim) | Hann 3.00 m / -32.0 dB; rect 1.85 m / -13.3 dB; one step, Hann 5.08 m | [`harris1978`] |
| Range ambiguity | $c/(2\delta f)=cT_c/2$; steady state $R<c(T_c-\tau_{sys})/2$ | 2998 m; ≈ 2.57 km |
| Doppler bin, resolution | $f_D=2v/\lambda$; bin $=f_DM\bar T_s \bmod M$; $1/(M\bar T_s)$ | 0.51 Hz at $M$=64 |
| Velocity span, resolution | $\pm\lambda/(4\bar T_s)$; $\lambda/(2M\bar T_s)$ | ±1.00 m/s; 3.1 cm/s |

There are no grating lobes at $c/(2\Delta f)$ = 5 m, because the sub-bands are contiguous.
$\bar T_s$ is the mean sweep period of the CPI, taken from the timestamps. **For b210-11:**
`compute_budget` uses `retune_s` = 9.2 ms and reports 36.40 ms (±0.84 m/s, 2.6 cm/s). Label that
value as planned; the measured period is shorter.

## 6. Target motion

![Fig. 2](figures/fig_motion.png)

*Fig. 2. Loss at the mid-CPI range. Each burst has its own range, timestamp (σ 0.34 ms) and LO
phase. M = 64, Hann in range and Doppler.*

When each sweep is stitched first, step $k$ carries an extra $2\pi f_DkT_{step}$. The loss is
about $\cos(\pi f_DT_{step})$: -0.7 dB at 0.5 m/s and -3.3 dB at 1 m/s. **Transform Doppler
first**, at each burst's own time:

$$D_k(f_D)=\sum_m w_m\,Z_{m,k}\,e^{-j2\pi f_D (t_{m,k}-\bar t)},\qquad f_D=j/(M\bar T_s),$$

then stitch per Doppler bin. This removes the step offset and the jitter. The loss stays under
0.2 dB up to $v_{amb}$, at a cost of about 12 M complex MACs per $M$ = 64 map. The per-sweep
stitched $Z$ still serves as the zero-Doppler preview profile.

What is ignored:

- **Aliasing.** When $|v|>v_{amb}$, the step compensation is wrong by $2\pi T_{step}/\bar T_s$
  (π for $N=2$), which splits the lobe.
- **Range walk over the CPI** [`perry1999`], and the matching $\nu/f_c$ Doppler scaling (±1.5 %).
  Both are negligible while $M\le\Delta R/(2|v|\bar T_s)$: 32 at 1 m/s, 65 at 0.5 m/s. See
  [`wehner1995`, ch. 5] for stepped-frequency velocity effects.
- **Platform motion.** The map assumes a parked radar. The no-stop-and-go rule still governs
  imaging.
- **Zero Doppler.** Leakage and static clutter sit at $f_D=0$. `remove_static_clutter` is a
  display toggle, off by default.

## 7. Other configs

For any $N$: $f_k=f_c+(k-\tfrac{N-1}{2})\Delta f$, and the split points fall midway between
adjacent step centres.

- **Raise an error** if $\Delta f/\delta f$ is not an integer.
- **Warn** if $\Delta f>0.9B$: the spectral gaps give grating lobes at multiples of $c/(2\Delta f)$
  [`levanon2004`; `wehner1995`].
- With zero overlap, the overlap check is lost.
- $\bar T_s\approx NT_{step}$, so $v_{amb}\propto1/N$.
- The envelope tolerance is $\delta\lesssim10°/(360°\,\Delta f)$.
- $N=1$ still needs per-burst normalization.

## 8. Test list for Step 3

Noise-free unless stated. The sim uses random $\phi_{m,k}$, $\tau_{sys}$ = 159.3 samples, and a
leakage path of 3 m.

1. `generate_chirp` against `chirp_replica.cf32`: maximum error ≤ 3e-5.
2. `average_chirps`: 50 periods return the pulse; with noise, SNR gain is 17 ± 0.5 dB.
3. `range_compress`: a delay of $d$ samples peaks at $d\times$upsample.
4. Stationary target at 37.3 m: peak within 0.2 m, zero-Doppler bin. A wrong $\tau_L$ sign gives
   34.3 m.
5. Random against coherent LO phase: peak loss < 0.1 dB noise-free, < 1 dB at 20 dB SNR; PSL
   ≤ -30 dB.
6. Negative control: an integer-rounded $\tau_{env}$ raises PSL by > 10 dB.
7. -3 dB width (Hann): 3.00 m ± 5 % stitched, 5.08 m ± 5 % for one step.
8. Two equal targets 3.0 m apart (rect) or 5.0 m apart (Hann): the stitched profile dips ≥ 1.5 dB
   at all 13 relative phases, and one step fails at some phase. Targets exactly $c/(2B_{tot})$
   apart are not reliably resolved under any window.
9. Target at 0.5 m/s, $M$ = 64: Doppler bin within ½ bin of $2vM\bar T_s/\lambda$; loss < 0.5 dB.
10. Test 9 repeated with the real `usrp_time_s` of `run_20261106_155516_rx`.
11. Leakage sits at $c\tau_L/2$ in the zero-Doppler bin. A DC offset with random per-burst phase
    stays ≤ noise + 1 dB off zero Doppler.
12. A non-integer $\Delta f/\delta f$ raises `ValueError`; $N$ = 3 at 15 MHz passes tests 4 and 7.

## Confidence

The derivation and the formulas are standard, and the model checks them: **high**. Not yet tested
on hardware:

- whether the antenna leakage is stable enough to serve as $c_{m,k}$;
- whether $\tau_{env}$ can be fit to 1 ns;
- antenna phase-centre drift with frequency.

The literature supports the method choices only: **medium-high**. Lord and Yuan are cited from
their abstracts, and Wehner and Levanon at chapter level.

## Sources

- `wehner1995` — Wehner, D. R. (1995), "High-Resolution Radar," 2nd ed., *Artech House*, ISBN 978-0-89006-727-7. url:https://us.artechhouse.com/High-Resolution-Radar-Second-Edition-P469.aspx
- `wilkinson1998` — Wilkinson, A. J., Lord, R. T., Inggs, M. R. (1998), "Stepped-frequency processing by reconstruction of target reflectivity spectrum," *Proc. COMSIG 1998*, pp. 101-104. doi:10.1109/COMSIG.1998.736930
- `lord1997` — Lord, R. T., Inggs, M. R. (1997), "High resolution SAR processing using stepped-frequencies," *IGARSS'97*, pp. 490-492. doi:10.1109/IGARSS.1997.615924
- `lord2000` — Lord, R. T. (2000), "Aspects of stepped-frequency processing for low-frequency SAR systems," *PhD thesis, University of Cape Town*. url:http://hdl.handle.net/11427/40264
- `nel2002` — Nel, W., Tait, J., Lord, R., Wilkinson, A. (2002), "The use of a frequency domain stepped frequency technique to obtain high range resolution on the CSIR X-band SAR system," *IEEE AFRICON 2002*, pp. 327-332. doi:10.1109/AFRCON.2002.1146857
- `yuan2009` — Yuan, H., Gao, M., Liu, G. (2009), "Coherent spectrum synthesis of frequency-stepped chirp signal," *IET Int. Radar Conf. 2009*. doi:10.1049/cp.2009.0271
- `levanon2004` — Levanon, N., Mozeson, E. (2004), "Radar Signals," *Wiley*. doi:10.1002/0471663085
- `harris1978` — Harris, F. J. (1978), "On the use of windows for harmonic analysis with the discrete Fourier transform," *Proc. IEEE* 66(1), pp. 51-83. doi:10.1109/PROC.1978.10837
- `perry1999` — Perry, R. P., DiPietro, R. C., Fante, R. L. (1999), "SAR imaging of moving targets," *IEEE Trans. AES* 35(1), pp. 188-200. doi:10.1109/7.745691
