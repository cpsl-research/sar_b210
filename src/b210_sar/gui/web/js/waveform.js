// "Waveform construction": two SVG views drawn from the backend's waveform_timeline JSON (planned timing only).
// Nothing is computed here beyond layout; every number comes from the API.
const NS = "http://www.w3.org/2000/svg";
export const STEP_COLORS = ["#4aa3ff", "#ff8a3d", "#4ccf7a", "#e05cc8", "#e6c84a", "#35d0d0", "#b08cff", "#ff6b6b"];
const W = 760;

function s(tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}
const mhz = (hz) => `${+(hz / 1e6).toFixed(2)} MHz`;
const ms = (t) => `${+(t * 1e3).toFixed(2)} ms`;
const us = (t) => `${+(t * 1e6).toFixed(2)} us`;
function svg(h) { const e = s("svg", { viewBox: `0 0 ${W} ${h}`, width: "100%", role: "img", class: "wf" }); return e; }

// Steps to draw: all if <= 4, else the first two, an ellipsis, and the last.
function shown(n) { return n <= 4 ? [...Array(n).keys()] : [0, 1, null, n - 1]; }

export function freqView(tl) {
  const H = 300, L = 92, R = W - 12, T = 16, B = 34;
  const g = svg(H);
  const lo0 = Math.min(tl.band_lo_hz, tl.permitted_band ? tl.permitted_band.min_hz : Infinity);
  const hi0 = Math.max(tl.band_hi_hz, tl.permitted_band ? tl.permitted_band.max_hz : -Infinity);
  const pad = (hi0 - lo0) * 0.12, lo = lo0 - pad, hi = hi0 + pad;
  const y = (f) => T + (H - T - B) * (1 - (f - lo) / (hi - lo));
  // y axis ticks
  for (let i = 0; i <= 4; i++) {
    const f = lo + (hi - lo) * i / 4;
    g.append(s("line", { x1: L, x2: R, y1: y(f), y2: y(f), stroke: "var(--line)", "stroke-dasharray": "2 4" }));
    g.append(s("text", { x: L - 6, y: y(f) + 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11 }, `${(f / 1e6).toFixed(1)}`));
  }
  g.append(s("text", { x: 4, y: 12, fill: "var(--muted)", "font-size": 11 }, "frequency (MHz)"));
  // permitted band limits
  if (tl.permitted_band) for (const f of [tl.permitted_band.min_hz, tl.permitted_band.max_hz]) {
    g.append(s("line", { x1: L, x2: R, y1: y(f), y2: y(f), stroke: "var(--bad)", "stroke-width": 1.2, "stroke-dasharray": "6 3" }));
    g.append(s("text", { x: R, y: y(f) + (f === tl.permitted_band.min_hz ? 12 : -4), "text-anchor": "end", fill: "var(--bad)", "font-size": 10 }, `permitted ${mhz(f)}`));
  }
  // stitched total band, shaded strip on the axis
  g.append(s("rect", { x: L + 2, y: y(tl.band_hi_hz), width: 14, height: y(tl.band_lo_hz) - y(tl.band_hi_hz), fill: "var(--acc)", opacity: 0.25, stroke: "var(--acc)" }));
  g.append(s("text", { x: L + 22, y: y(tl.band_lo_hz) - 4, fill: "var(--acc)", "font-size": 11 }, `B_tot ${mhz(tl.total_band_hz)}`));
  // overlap between step 0 and 1
  if (tl.steps.length > 1 && tl.overlap_hz > 0) {
    const a = tl.steps[1].lo_hz, b = tl.steps[0].hi_hz;
    g.append(s("rect", { x: L + 2, y: y(b), width: 14, height: y(a) - y(b), fill: "var(--warn)", opacity: 0.55 }));
    g.append(s("text", { x: L + 22, y: y(b) + 11, fill: "var(--warn)", "font-size": 10 }, `overlap ${mhz(tl.overlap_hz)}`));
  }
  // steps: 3 chirps each, then a break
  const idx = shown(tl.steps.length), cw = 34, gap = 46, x0 = L + 150;
  let x = x0;
  const nShow = Math.min(3, Math.max(1, Math.round(tl.chirps_per_dwell)));
  for (const k of idx) {
    if (k === null) { g.append(s("text", { x: x + 8, y: (T + H - B) / 2, fill: "var(--muted)", "font-size": 16 }, "...")); x += 36; continue; }
    const st = tl.steps[k], col = STEP_COLORS[k % STEP_COLORS.length], xs = x;
    g.append(s("rect", { x: xs, y: y(st.hi_hz), width: nShow * cw, height: y(st.lo_hz) - y(st.hi_hz), fill: col, opacity: 0.12 }));
    for (let c = 0; c < nShow; c++) {
      const xa = xs + c * cw, tp = cw * tl.taper_fraction;
      const ramp = `${xa},${y(st.lo_hz)} ${xa + cw},${y(st.hi_hz)}`;
      g.append(s("polyline", { points: ramp, fill: "none", stroke: col, "stroke-width": 2 }));
      g.append(s("line", { x1: xa + cw, y1: y(st.hi_hz), x2: xa + cw, y2: y(st.lo_hz), stroke: col, "stroke-width": 1, opacity: 0.35 }));
      g.append(s("line", { x1: xa, x2: xa + Math.max(tp, 1.5), y1: y(st.lo_hz) + 3, y2: y(st.lo_hz) + 3, stroke: "var(--muted)", "stroke-width": 2 }));
      g.append(s("line", { x1: xa + cw - Math.max(tp, 1.5), x2: xa + cw, y1: y(st.hi_hz) - 3, y2: y(st.hi_hz) - 3, stroke: "var(--muted)", "stroke-width": 2 }));
    }
    g.append(s("text", { x: xs, y: y(st.hi_hz) - 7, fill: col, "font-size": 11 }, `step ${k}  centre ${(st.centre_hz / 1e6).toFixed(2)} MHz`));
    g.append(s("text", { x: xs, y: H - B + 14, fill: "var(--muted)", "font-size": 10 }, `${nShow} of ${+tl.chirps_per_dwell.toFixed(1)} chirps, ${us(tl.timing.chirp_length_s)} each`));
    x += nShow * cw;
    g.append(s("text", { x: x + 6, y: (T + H - B) / 2 + 20, fill: "var(--muted)", "font-size": 13 }, "//"));
    x += gap;
  }
  g.append(s("text", { x: R, y: H - 4, "text-anchor": "end", fill: "var(--muted)", "font-size": 11 }, "time (not to scale; each step dwells ~" + ms(tl.timing.dwell_s) + ", chirp bandwidth " + mhz(tl.chirp_bandwidth_hz) + ", spacing " + mhz(tl.step_spacing_hz) + ")"));
  return g;
}

export function timingView(tl) {
  const H = 146, L = 12, R = W - 12, T = 48;
  const g = svg(H);
  const t = tl.timing, idx = shown(tl.steps.length).filter((k) => k !== null).slice(0, 4);
  const drawn = Math.min(tl.steps.length, 4), total = t.step_time_s * drawn;
  const px = (R - L) / total;
  let x = L;
  for (let i = 0; i < drawn; i++) {
    const col = STEP_COLORS[i % STEP_COLORS.length];
    for (const [name, dur, fill] of [["retune", t.retune_s, "var(--warn)"], ["wait", t.wait_s, "var(--muted)"], ["dwell", t.dwell_s, col]]) {
      const w = dur * px;
      g.append(s("rect", { x, y: T, width: Math.max(w, 1), height: 34, fill, opacity: name === "dwell" ? 0.85 : 0.55, stroke: "var(--bg)" }));
      if (w > 62) g.append(s("text", { x: x + w / 2, y: T + 21, "text-anchor": "middle", fill: "#0d1117", "font-size": 11, "font-weight": 600 }, `${name} ${ms(dur)}`));
      else if (w > 24) g.append(s("text", { x: x + w / 2, y: T + 21, "text-anchor": "middle", fill: "#0d1117", "font-size": 10, "font-weight": 600 }, name));
      x += w;
    }
    g.append(s("text", { x: x - t.step_time_s * px / 2, y: T - 8, "text-anchor": "middle", fill: col, "font-size": 11 }, `step ${i}: ${ms(t.step_time_s)}`));
  }
  const more = tl.steps.length - drawn;
  // sweep period bracket
  const sw = more > 0 ? "sweep = " + tl.num_steps + " x " + ms(t.step_time_s) : "sweep";
  g.append(s("line", { x1: L, x2: R, y1: T + 52, y2: T + 52, stroke: "var(--fg)" }));
  g.append(s("line", { x1: L, x2: L, y1: T + 46, y2: T + 58, stroke: "var(--fg)" }));
  g.append(s("line", { x1: R, x2: R, y1: T + 46, y2: T + 58, stroke: "var(--fg)" }));
  g.append(s("text", { x: (L + R) / 2, y: T + 70, "text-anchor": "middle", fill: "var(--fg)", "font-size": 12 },
    `${sw} period ${ms(t.sweep_time_s)} (planned)` + (more > 0 ? `; first ${drawn} steps drawn` : "")));
  g.append(s("text", { x: L, y: 12, fill: "var(--muted)", "font-size": 11 }, `time within a sweep: retune | wait/settle | dwell (${+tl.chirps_per_dwell.toFixed(1)} chirps)`));
  return g;
}

export function marks(tl) {
  const d = tl.derived, box = document.createElement("div");
  box.className = "marks";
  for (const [k, v] of [["B_tot", mhz(tl.total_band_hz)], ["range res", `${d.range_resolution_m.toFixed(2)} m`],
                        ["R_amb", `${Math.round(d.max_unambiguous_range_m)} m`], ["v_amb", `+/-${d.max_unambiguous_velocity_mps.toFixed(3)} m/s`],
                        [`dv (M=${d.num_sweeps})`, `${(d.velocity_resolution_mps * 100).toFixed(2)} cm/s`]]) {
    const c = document.createElement("span"); c.className = "mark";
    const a = document.createElement("b"); a.textContent = v; c.append(a, ` ${k}`); box.append(c);
  }
  return box;
}

export function drawWaveform(root, tl) {
  root.replaceChildren();
  if (!tl) { const p = document.createElement("p"); p.className = "empty"; p.textContent = "No diagram: fix the errors below (the config does not parse)."; root.append(p); return; }
  const a = document.createElement("div"), b = document.createElement("div");
  a.append(freqView(tl)); b.append(timingView(tl));
  root.append(marks(tl), a, b);
}
