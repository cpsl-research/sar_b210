// File view: picker, report banner, transport controls, delay-Doppler heatmap (binary /dd) and range profile.
import { api } from "./api.js";

const $ = (id) => document.getElementById(id);
let meta = null, frame = null, mapHdr = null, mapDb = null, busy = false;
const off = document.createElement("canvas");

function colour(t) { // dark blue -> teal -> yellow -> white
  t = Math.min(Math.max(t, 0), 1);
  const stops = [[0, [10, 10, 40]], [0.35, [40, 60, 150]], [0.6, [30, 170, 150]], [0.85, [240, 220, 60]], [1, [255, 255, 255]]];
  for (let i = 1; i < stops.length; i++) if (t <= stops[i][0]) {
    const [t0, a] = stops[i - 1], [t1, b] = stops[i], f = (t - t0) / (t1 - t0);
    return a.map((v, k) => Math.round(v + (b[k] - v) * f));
  }
  return stops[4][1];
}

function levels() {
  const span = parseFloat($("fv-dbspan").value) || 50;
  const topv = parseFloat($("fv-dbtop").value);
  const top = Number.isFinite(topv) ? topv : mapHdr.db_max;
  return [top - span, top];
}

function drawMap() {
  const c = $("fv-dd"), g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  g.fillStyle = "#0e1114"; g.fillRect(0, 0, c.width, c.height);
  if (!mapHdr) { g.fillStyle = "#8b97a3"; g.font = "13px sans-serif"; g.fillText(frame && frame.map_note ? frame.map_note : "no delay-Doppler map yet", 20, 30); return; }
  const [D, R] = mapHdr.shape, [lo, hi] = levels();
  off.width = R; off.height = D;
  const img = off.getContext("2d").createImageData(R, D);
  for (let d = 0; d < D; d++) for (let r = 0; r < R; r++) {
    const [cr, cg, cb] = colour((mapDb[d * R + r] - lo) / (hi - lo));
    const o = ((D - 1 - d) * R + r) * 4; // row 0 = lowest velocity at the bottom
    img.data[o] = cr; img.data[o + 1] = cg; img.data[o + 2] = cb; img.data[o + 3] = 255;
  }
  off.getContext("2d").putImageData(img, 0, 0);
  const L = 60, T = 10, W = c.width - L - 70, H = c.height - T - 40;
  g.imageSmoothingEnabled = false;
  g.drawImage(off, L, T, W, H);
  g.strokeStyle = "#8b97a3"; g.fillStyle = "#dde3e8"; g.font = "11px sans-serif"; g.strokeRect(L, T, W, H);
  const [r0, r1] = mapHdr.ranges_m, v = mapHdr.velocity_mps, v0 = v[0], v1 = v[D - 1];
  const step = (span, n) => { const raw = span / n, p = 10 ** Math.floor(Math.log10(raw)); return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw); };
  const sx = step(r1 - r0, 8);
  for (let x = Math.ceil(r0 / sx) * sx; x <= r1; x += sx) { const px = L + (x - r0) / (r1 - r0) * W; g.beginPath(); g.moveTo(px, T + H); g.lineTo(px, T + H + 4); g.stroke(); g.fillText(x.toFixed(0), px - 8, T + H + 16); }
  const sy = step(v1 - v0, 6);
  for (let y = Math.ceil(v0 / sy) * sy; y <= v1; y += sy) { const py = T + H - (y - v0) / (v1 - v0) * H; g.beginPath(); g.moveTo(L - 4, py); g.lineTo(L, py); g.stroke(); g.fillText(y.toFixed(2), 24, py + 4); }
  g.fillText("range (m)", L + W / 2 - 20, c.height - 6);
  g.save(); g.translate(11, T + H / 2 + 50); g.rotate(-Math.PI / 2); g.fillText("closing velocity (m/s)", 0, 0); g.restore();
  for (let i = 0; i < H; i++) { const [cr, cg, cb] = colour(1 - i / H); g.fillStyle = `rgb(${cr},${cg},${cb})`; g.fillRect(L + W + 12, T + i, 12, 1); }
  g.fillStyle = "#dde3e8"; g.fillText(hi.toFixed(0) + " dB", L + W + 28, T + 10); g.fillText(lo.toFixed(0) + " dB", L + W + 28, T + H);
}

function drawProfile() {
  const c = $("fv-prof"), g = c.getContext("2d");
  g.fillStyle = "#0e1114"; g.fillRect(0, 0, c.width, c.height);
  if (!meta || !meta.ranges_m || !frame || !frame.profile_db) return;
  const r = meta.ranges_m, p = frame.profile_db, L = 60, T = 8, W = c.width - L - 70, H = c.height - T - 30;
  const [lo, hi] = [Math.min(...p), Math.max(...p)];
  g.strokeStyle = "#8b97a3"; g.strokeRect(L, T, W, H);
  g.strokeStyle = "#4aa3df"; g.beginPath();
  p.forEach((y, i) => { const px = L + (r[i] - r[0]) / (r[r.length - 1] - r[0]) * W, py = T + H - (y - lo) / (hi - lo || 1) * H; i ? g.lineTo(px, py) : g.moveTo(px, py); });
  g.stroke();
  g.fillStyle = "#dde3e8"; g.font = "11px sans-serif";
  g.fillText(`${hi.toFixed(0)} dB`, 8, T + 10); g.fillText(`${lo.toFixed(0)} dB`, 8, T + H);
  g.fillText(`range (m): ${r[0].toFixed(0)} .. ${r[r.length - 1].toFixed(0)}   profile of sweep ${frame.sweep} (stitch-first, not Doppler-processed)`, L, c.height - 8);
}

function renderReport() {
  const el = $("fv-report");
  if (!meta) { el.className = "banner"; return; }
  const r = meta.report;
  let cls = r.ok ? "ok" : "warn", html = `<b>${meta.name}</b>: ${r.summary}`;
  const bits = [];
  if (r.incomplete_count) bits.push(`${r.incomplete_count} incomplete sweep(s): ${Object.entries(r.incomplete).slice(0, 8).map(([k, v]) => `#${k} missing step ${v.join(",")}`).join("; ")}${r.incomplete_count > 8 ? " ..." : ""}`);
  if (r.invalid_count) bits.push(`${r.invalid_count} INVALID burst(s): ${r.invalid_bursts.slice(0, 8).join(",")}`);
  if (r.error_flag_count) bits.push(`${r.error_flag_count} burst(s) with error flags`);
  if (r.duplicates) bits.push(`${r.duplicates} duplicate(s)`);
  if (r.unexpected) bits.push(`${r.unexpected} unexpected-step burst(s)`);
  if (r.truncated) bits.push(`${r.truncated} truncated burst(s) (IQ missing)`);
  bits.push(...r.notes);
  if (meta.error) { bits.push(meta.error); cls = "bad"; }
  if (r.invalid_count || r.truncated || !r.config_known) cls = "bad";
  if (!meta.calibrated) bits.push("no leakage reference: ranges are relative to the leakage / uncalibrated (fine for RX-only data)");
  el.className = `banner show ${cls}`;
  el.innerHTML = html + (bits.length ? "<br>" + bits.map((b) => `&bull; ${b}`).join("<br>") : "");
}

function renderMeta() {
  if (!meta || !frame) return;
  const f = frame, m = meta;
  $("fv-empty").hidden = true; $("fv-dd").hidden = false; $("fv-prof").hidden = false; $("fv-controls").hidden = false;
  $("fv-title").textContent = `${m.name}  sweep ${f.sweep} (${f.pos + 1}/${f.n})`;
  const pos = $("fv-pos"); pos.max = f.n - 1; pos.min = 0; pos.value = f.pos;
  $("fv-posl").textContent = `${f.pos + 1} / ${f.n}`;
  $("fv-play").textContent = f.playing ? "pause" : "play";
  const rows = [`range resolution ${m.range_resolution_m ? m.range_resolution_m.toFixed(2) : "-"} m (B_tot ${m.total_bandwidth_hz ? (m.total_bandwidth_hz / 1e6).toFixed(0) : "-"} MHz), unambiguous ${m.max_unambiguous_range_m ? m.max_unambiguous_range_m.toFixed(0) : "-"} m`];
  if (f.sweep_period_ms) rows.push(`CPI sweeps ${f.cpi_sweeps[0]}..${f.cpi_sweeps[1]} (M=${f.m}, ${f.window}); t=${f.t_s.toFixed(2)} s; sweep period ${f.sweep_period_ms.toFixed(2)} ms (${f.basis}); velocity resolution ${f.velocity_resolution_mps.toFixed(3)} m/s, span +/-${f.velocity_span_mps.toFixed(2)} m/s; missing bursts in CPI: ${f.missing_bursts}`);
  if (f.profile_peak) rows.push(`profile peak ${f.profile_peak.db.toFixed(1)} dB at ${f.profile_peak.range_m.toFixed(1)} m`);
  if (f.error) rows.push(`<span class="bad">${f.error}</span>`);
  $("fv-meta").innerHTML = rows.join("<br>");
  $("fv-steps").innerHTML = "burst validity (this sweep): " + (f.valid_steps || []).map((v, k) => `<span class="${v ? "ok" : "bad"}">step ${k} ${v ? "ok" : "MISSING/INVALID"}</span>`).join("");
}

export function onMessage(m) {
  if (m.type === "file") { meta = m; mapHdr = null; syncParams(m.params); renderReport(); drawProfile(); drawMap(); }
  else if (m.type === "frame") { frame = m; if (m.map_note) mapHdr = null; renderMeta(); drawProfile(); if (!mapHdr) drawMap(); }
}

function syncParams(p) {
  if (!p) return;
  const set = (id, v) => { if (document.activeElement !== $(id)) $(id).value = v ?? ""; };
  set("fv-m", p.m); set("fv-window", p.window); set("fv-hop", p.hop); set("fv-rate", p.rate_hz); set("fv-maxr", p.max_range_m); set("fv-leak", p.leakage_m);
  $("fv-static").checked = !!p.remove_static;
}

function connectDD() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/dd`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) => {
    const dv = new DataView(ev.data), n = dv.getUint32(0, true);
    mapHdr = JSON.parse(new TextDecoder().decode(new Uint8Array(ev.data, 4, n)));
    const off4 = 4 + n, cnt = mapHdr.shape[0] * mapHdr.shape[1];
    mapDb = new Float32Array(ev.data.slice(off4, off4 + cnt * 4));
    drawMap();
  };
  ws.onclose = () => setTimeout(connectDD, 2000);
}

async function list(path) {
  const { status, data } = await api(`/api/files?path=${encodeURIComponent(path || "")}`);
  const ul = $("fv-files"); ul.innerHTML = "";
  if (status !== 200) { $("fv-msg").textContent = (data && data.detail) || `error ${status}`; return; }
  $("fv-msg").textContent = data.entries.length ? "" : "no run folders or .h5 files here (data roots are set with --data-root)";
  $("fv-dir").textContent = data.path || "data roots";
  $("fv-up").hidden = !data.path;
  $("fv-up").onclick = () => list(data.parent || "");
  for (const e of data.entries) {
    const li = document.createElement("li"); li.className = e.kind;
    li.textContent = (e.kind === "dir" ? "[dir] " : e.kind === "run" ? "[run] " : "[h5] ") + e.name;
    li.onclick = () => (e.kind === "dir" ? list(e.path) : openPath(e.path));
    ul.appendChild(li);
  }
}

export async function openPath(path) {
  $("fv-msg").textContent = "opening...";
  const { status, data } = await api("/api/open", { method: "POST", body: { path } });
  $("fv-msg").textContent = status === 200 ? "" : `refused: ${(data && data.detail) || status}`;
  $("fv-msg").className = status === 200 ? "muted small" : "bad small";
}

async function control(body) { const r = await api("/api/control", { method: "POST", body }); if (r.status !== 200) $("fv-msg").textContent = (r.data && r.data.detail) || r.status; }

export async function initFileView() {
  connectDD();
  $("fv-play").onclick = () => control({ action: frame && frame.playing ? "pause" : "play" });
  $("fv-step").onclick = () => control({ action: "step" });
  $("fv-back").onclick = () => control({ action: "back" });
  $("fv-pos").onchange = (e) => control({ action: "seek", pos: +e.target.value });
  const bind = (id, key, conv) => $(id).addEventListener("change", (e) => control({ action: "set", [key]: conv(e.target) }));
  bind("fv-m", "m", (t) => +t.value); bind("fv-window", "window", (t) => t.value); bind("fv-hop", "hop", (t) => +t.value);
  bind("fv-rate", "rate_hz", (t) => +t.value); bind("fv-maxr", "max_range_m", (t) => +t.value);
  bind("fv-leak", "leakage_m", (t) => (t.value === "" ? null : +t.value)); bind("fv-static", "remove_static", (t) => t.checked);
  $("fv-dbspan").onchange = $("fv-dbtop").onchange = () => drawMap();
  const q = new URLSearchParams(location.search).get("source");
  const { data } = await api("/api/file");
  if (data && data.meta) { onMessage(data.meta); if (data.frame) onMessage(data.frame); }
  await list("");
  if (q) await openPath(q);
}
