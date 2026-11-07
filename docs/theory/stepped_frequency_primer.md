# Stepped-frequency radar: a primer for sar_b210

All numbers are for `configs/default.yaml`. Formulas are those of
[`delay_doppler.md`](../delay_doppler.md) §5; derivations are in its §2-§4 and are not repeated here.
Every number below is printed by `docs/figures/stepped_frequency_primer_figures.py` from `b210_sar.budget`.

## 1. One chirp: delay becomes frequency

The radar transmits a linear sweep (a chirp) of bandwidth $B$ over a time $T_c$. An echo from a target is the
same sweep, late by the round-trip delay $\tau$. At any instant the echo is therefore at a slightly different
frequency than what is being sent, and that difference is proportional to $\tau$ (Fig. 1). Mixing the echo with the
transmit signal turns it into a tone; matched filtering it (what sar_b210 does, §2 of the method doc) gives a
peak. Either way delay maps to a frequency or peak position, and range is $R=c\tau/2$.

![Fig. 1](../figures/fig_sfp_chirp.png)

*Fig. 1. TX chirp and a delayed echo. The vertical gap between them is the beat frequency.*

Default: $B$ = 45 MHz over $T_c$ = 20 µs, so 2.25 THz/s, or 15 kHz of beat per metre of range. One such chirp alone
resolves 3.33 m.

## 2. Stepping and stitching: more band than one chirp can carry

Range resolution comes from bandwidth, and one chirp is limited to what the radio passes: about 56 MHz at
56 MS/s, of which the default uses 45 MHz. So the radar retunes its centre frequency by $\Delta f$,
sends the next chirp, and glues the sub-bands side by side in the frequency domain (Fig. 2). The occupied
band is $B_{tot}=(N-1)\Delta f+B$. The sub-bands overlap on purpose, so that the join has a guard region (§4 of the method doc
does the stitching).

![Fig. 2](../figures/fig_sfp_stitch.png)

*Fig. 2. Two 45 MHz sub-bands 30 MHz apart, overlapping by 15 MHz, make one 75 MHz band.*

Default: $N$ = 2 steps, $\Delta f$ = 30 MHz, so $B_{tot}$ = 75 MHz.

## 3. Range resolution, maximum range, and the gap rule

Resolution is set by the stitched band alone: $\Delta R=c/(2B_{tot})$. The farthest unambiguous echo is set by
the chirp length: $R_{amb}=cT_c/2$, because an echo later than $T_c$ wraps into the next chirp.

The sub-bands must touch or overlap, $\Delta f\le B$. If they do not, the stitched spectrum has a hole, and the
range profile grows extra lobes (grating lobes) near $c/(2\Delta f)$ that can pass for targets (Fig. 3). The code
warns above $\Delta f>0.9B$ (40.5 MHz here), leaving a margin at the edges (method doc A.2).

![Fig. 3](../figures/fig_sfp_grating.png)

*Fig. 3. Range profile of a point target, flat spectrum, no window (illustrative, not the processing chain). Left: contiguous band, first sidelobe −13.5 dB.
Right: 15 MHz gap, a lobe at −7.8 dB near $c/(2\Delta f)$ = 2.50 m.*

Default: $\Delta R$ = 2.00 m (3.33 m with one step), $R_{amb}$ = 2998 m. $\Delta f/B$ = 0.67, so no gaps.

## 4. Doppler across sweeps: speed from slow time

A moving target changes its delay a little from sweep to sweep, which rotates the echo's phase. A **sweep** is one
pass through all $N$ steps, and takes a time $T_s$. Watch one range bin over $M$ sweeps (the coherent processing
interval) and the phase rotation is a Doppler frequency, just as range was a frequency within a chirp. Because
sweeps are sampled once per $T_s$, speed is unambiguous only up to $v_{amb}=\pm\lambda/(4T_s)$ (faster targets
alias), and the resolution is $\lambda/(2MT_s)$. A slow sweep hurts the first; a short CPI hurts the second (Fig. 4).

![Fig. 4](../figures/fig_sfp_velocity.png)

*Fig. 4. Left: $v_{amb}$ against $T_s$. Right: velocity resolution against $M$.*

Default: $\lambda$ = 12.28 cm. With the measured $T_s$ below and $M$ = 64: $v_{amb}$ = ±1.00 m/s, resolution 3.1 cm/s.

## 5. The time budget: where $T_s$ comes from

Each step costs three things, in order: **retune** (the synthesizer moves and locks), **wait** (the LO settles and
the timed commands lead the burst), and **dwell** (the burst itself: 50 chirps averaged, 17 dB gain). The sweep
is $N$ steps of that (Fig. 5), so every design choice lands on $T_s$:

- more steps $N$ give more band but a proportionally slower sweep, so a smaller $v_{amb}$;
- a longer dwell gives more energy per step and a slower sweep;
- the sample rate is bounded by USB 3: 56 MS/s of sc16 is 224 MB/s, against a nominal ceiling of 61.44 MS/s
  (245.76 MB/s, `configs/hw_limits.yaml`); the practical sustained rate is not measured.

![Fig. 5](../figures/fig_sfp_timing.png)

*Fig. 5. Left: one step, planned (from the config) and measured (RX-only run). Right: planned sweep time against $N$, with $v_{amb}$.*

Default: planned step 9.2 + 8.0 + 1.0 = 18.2 ms, so $T_s$ = 36.40 ms.

## 6. Worked example: `configs/default.yaml`

| Quantity | Value |
|---|---|
| $N$, $\Delta f$, $B$ | 2, 30 MHz, 45 MHz |
| $f_s$, $T_c$, dwell | 56 MS/s, 20 µs, 1 ms (50 chirps) |
| $B_{tot}$; $\Delta R$ | 75 MHz; 2.00 m |
| $R_{amb}$ | 2998 m |
| $T_s$ planned / measured | 36.40 ms / 30.637 ms |
| $v_{amb}$ planned / measured | ±0.84 m/s / ±1.00 m/s |
| Velocity resolution at $M$ = 64 | 2.6 cm/s / 3.1 cm/s |

**Why the measured sweep is shorter than the planned one.** The planned $T_s$ is the config's `retune_s` + wait + dwell,
per step. The measured one is the mean step-0 to step-0 gap in `bursts.csv` of `run_20261106_155516_rx`
(980 sweeps, σ 0.34 ms): 15.32 ms per step, 2.88 ms less than the 18.2 ms planned. Wait and dwell are set by the
config, so the difference sits in the retune: 15.32 − 8 − 1 = 6.32 ms against the 9.2 ms in `retune_s`. `docs/design_notes.md` records a TX+RX bench run with a median tune of 6.6 ms but a median burst spacing of
18.3 ms, which matches the plan. The repo does not explain the gap itself, so: HYPOTHESIS: every `_rx` run
retunes RX only (TX is off), `retune_s` was measured with TX and RX together, and the
RX-only step is faster. Test: back-to-back TX+RX and RX-only runs at the default config, comparing `step_period_s`. Until then, treat ±1.00 m/s as the RX-only value and ±0.84 m/s as the safe planning figure.
