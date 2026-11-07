import { api } from "./api.js";
import { drawWaveform } from "./waveform.js";

const $ = (id) => document.getElementById(id);
const FIELDS = [
  ["waveform.num_steps", "num_steps", ""], ["waveform.step_spacing_hz", "step_spacing_hz", "Hz"],
  ["waveform.chirp_bandwidth_hz", "chirp_bandwidth_hz", "Hz"], ["waveform.chirp_length_s", "chirp_length_s", "s"],
  ["waveform.dwell_s", "dwell_s", "s"], ["waveform.taper_fraction", "taper_fraction", ""],
  ["waveform.sample_rate_hz", "sample_rate_hz", "Hz"], ["rf.rx_gain_db", "rx_gain_db", "dB"],
  ["rf.tx_gain_db", "tx_gain_db", "dB"],
];
let selected = null;   // {name, kind: shipped|user|draft, yaml, read_only}
let editing = false;
let lastReport = null;
let genResult = null;
let timer = null;

function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function num(v, d = 3) { if (v === null) return "n/a (unset)"; return typeof v === "number" ? (Math.abs(v) >= 1e5 || (v !== 0 && Math.abs(v) < 1e-3) ? v.toExponential(3) : +v.toFixed(d)) : String(v); }

async function loadList() {
  const { data } = await api("/api/configs");
  const tb = $("cfg-list").tBodies[0];
  tb.replaceChildren();
  for (const c of data.configs) {
    const tr = el("tr");
    tr.dataset.name = c.name; tr.dataset.kind = c.kind;
    tr.append(el("td", c.name), el("td", c.kind), el("td", c.valid ? "valid" : "INVALID", c.valid ? "ok" : "bad"));
    const b = c.derived && c.derived.budget;
    tr.append(el("td", b ? `${num(b.sweep_time_s * 1000, 1)} ms` : "-", "muted"));
    if (!c.valid) tr.title = c.errors.join("\n");
    tr.addEventListener("click", () => select(c.name, c.kind));
    if (selected && selected.name === c.name && selected.kind === c.kind) tr.classList.add("sel");
    tb.append(tr);
  }
  $("limits-path").textContent = `hardware limits: ${data.hw_limits_path}`;
  const sel = $("gen-base"), keep = sel.value;
  sel.replaceChildren(...data.configs.filter((c) => c.valid).map((c) => { const o = el("option", c.name); o.value = c.name; return o; }));
  if (keep) sel.value = keep;
}

function showIssues(rep) {
  const box = $("cfg-issues");
  box.replaceChildren();
  for (const i of rep.errors) box.append(el("p", `error: ${i.field}: ${i.message}`, "bad"));
  for (const i of rep.warnings) box.append(el("p", `warning: ${i.field}: ${i.message}`, "warn"));
  if (!rep.errors.length && !rep.warnings.length) box.append(el("p", "no issues", "muted"));
}

function showDerived(rep) {
  const tb = $("cfg-derived").tBodies[0];
  tb.replaceChildren();
  if (!rep.derived) { $("basis").textContent = ""; tb.append(el("tr")); return; }
  $("basis").textContent = "basis: planned (no run folder)";
  const rows = [];
  for (const [k, v] of Object.entries(rep.derived.budget)) rows.push([`budget.${k}`, v]);
  const dd = rep.derived.delay_doppler.planned;
  for (const [k, v] of Object.entries(dd)) rows.push([`delay_doppler.${k}`, v]);
  for (const [k, v] of rows) {
    if (typeof v === "object" && v !== null) continue;
    const tr = el("tr"); tr.append(el("td", k), el("td", num(v))); tb.append(tr);
  }
}

function badges(rep, kind) {
  const b = $("cfg-badges");
  b.replaceChildren(el("span", kind === "shipped" ? "shipped (read-only)" : "user", "badge"),
                    el("span", rep.valid ? "valid" : "INVALID", `badge ${rep.valid ? "ok" : "bad"}`));
}

function renderView(rep, text) {
  lastReport = rep;
  badges(rep, selected.kind === "draft" ? "draft (unsaved)" : selected.kind);
  showIssues(rep); showDerived(rep);
  drawWaveform($("cfg-wave"), rep.timeline);
  $("btn-targets").hidden = !rep.timeline;
  $("cfg-yaml").textContent = text;
  const bad = !rep.valid;
  $("anyway-wrap").hidden = !bad || !editing;
  if (!bad) $("cfg-anyway").checked = false;
  $("btn-save").disabled = !editing || (bad && !$("cfg-anyway").checked);
}

function setEditing(on) {
  editing = on;
  for (const i of $("cfg-fields").querySelectorAll("input")) i.disabled = !on;
  $("cfg-saveline").hidden = !on;
  $("ro-note").textContent = on ? "(editing a draft; shipped files are never written)" : selected.read_only ? "(shipped: read-only, copy to edit)" : "(saved config: copy to edit)";
}

async function select(name, kind) {
  const { status, data } = await api(`/api/configs/${encodeURIComponent(name)}?kind=${kind}`);
  if (status !== 200) { $("cfg-title").textContent = data.error; return; }
  selected = { name: data.name, kind: data.kind, yaml: data.yaml, read_only: data.read_only };
  $("cfg-title").textContent = data.name;
  $("cfg-body").hidden = false;
  $("btn-copy").hidden = false; $("btn-delete").hidden = data.read_only;
  buildForm(data.values);
  setEditing(false);
  renderView(data.report, data.yaml);
  for (const tr of $("cfg-list").tBodies[0].rows) tr.classList.toggle("sel", tr.dataset.name === name && tr.dataset.kind === kind);
}

function buildForm(values) {
  const box = $("cfg-fields");
  box.replaceChildren();
  for (const [key, label, unit] of FIELDS) {
    const l = el("label"); l.append(el("span", `${label} ${unit}`.trim()));
    const i = el("input"); i.name = key; i.value = values[key] ?? ""; i.inputMode = "decimal"; i.disabled = true;
    i.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 150); });
    l.append(i); box.append(l);
  }
}

function formFields() {
  const f = {};
  for (const i of $("cfg-fields").querySelectorAll("input")) {
    const v = Number(i.value);
    if (i.value.trim() === "" || !Number.isFinite(v)) throw new Error(`${i.name}: enter a number`);
    f[i.name] = v;
  }
  return f;
}

// Live recompute: every edit (debounced 150 ms) re-checks the draft and redraws the diagram.
async function check() {
  let fields;
  try { fields = formFields(); } catch (e) { $("cfg-issues").replaceChildren(el("p", e.message, "bad")); $("btn-save").disabled = true; return null; }
  const { data } = await api("/api/configs/check", { method: "POST", body: { yaml: selected.yaml, fields } });
  if (!data || !data.ok) { $("cfg-issues").replaceChildren(el("p", data ? data.error : "check failed", "bad")); $("btn-save").disabled = true; return null; }
  renderView(data.report, data.yaml);
  return { data, fields };
}

async function save() {
  const name = $("cfg-newname").value.trim();
  if (!name) { $("cfg-issues").replaceChildren(el("p", "enter a name to save as", "bad")); return; }
  const c = await check();
  if (!c) return;
  const anyway = !c.data.report.valid && $("cfg-anyway").checked;
  const { status, data } = await api("/api/configs", { method: "POST", body: { name, yaml: selected.yaml, fields: c.fields, allow_invalid: anyway } });
  if (status !== 201) { $("cfg-issues").append(el("p", `not saved: ${data.error}`, "bad")); return; }
  await loadList();
  await select(data.name, "user");
  $("cfg-issues").prepend(el("p", anyway ? `saved as ${data.name}, flagged INVALID (capture and launch will refuse it)` : `saved as ${data.name}`, anyway ? "warn" : "ok"));
}

async function del() {
  if (!selected || selected.read_only) return;
  if (!window.confirm(`Delete user config "${selected.name}"? This cannot be undone.`)) return;
  const { status, data } = await api(`/api/configs/${encodeURIComponent(selected.name)}?confirm=${encodeURIComponent(selected.name)}`, { method: "DELETE" });
  if (status !== 200) { $("cfg-issues").replaceChildren(el("p", data.error, "bad")); return; }
  selected = null;
  $("cfg-body").hidden = true; $("cfg-title").textContent = "Select a config"; $("cfg-badges").replaceChildren();
  await loadList();
}

function num2(id) { const v = $(id).value.trim(); return v === "" ? null : Number(v); }

async function generate() {
  const body = { range_res_m: num2("gen-rr"), max_range_m: num2("gen-rmax"), max_velocity_mps: num2("gen-vmax"),
                 chirps_per_dwell: num2("gen-nchirps"), center_freq_hz: num2("gen-fc"), num_steps: num2("gen-nsteps"), base: $("gen-base").value || "default" };
  $("gen-msg").textContent = "generating...";
  const { data } = await api("/api/configs/generate", { method: "POST", body });
  if (!data || data.error) { $("gen-msg").textContent = ""; $("gen-result").hidden = false; $("gen-table").hidden = true; $("btn-gen-load").hidden = true;
    $("gen-issues").replaceChildren(el("p", data ? data.error : "generate failed", "bad")); return; }
  genResult = data;
  $("gen-msg").textContent = data.ok ? "ok" : "has errors (see below)";
  $("gen-result").hidden = false; $("gen-table").hidden = false; $("btn-gen-load").hidden = false;
  const a = data.achieved, t = data.targets, tb = $("gen-table").tBodies[0];
  tb.replaceChildren();
  const rows = [["range resolution (m)", t.range_res_m, a && a.range_res], ["max range R_amb (m)", t.max_range_m, a && a.R_amb],
                ["max velocity v_amb (m/s)", t.max_velocity_mps, a && a.v_amb], ["total band (MHz)", null, a && a.B_tot / 1e6],
                ["steps", null, a && a.num_steps], ["chirps per dwell", t.chirps_per_dwell, a && a.chirps_per_dwell], ["sweep time (ms)", null, a && a.sweep_time_s * 1e3]];
  for (const [k, tv, av] of rows) { const tr = el("tr"); tr.append(el("td", k), el("td", tv === null || tv === undefined ? "-" : num(tv)), el("td", av === null || av === undefined ? "-" : num(av))); tb.append(tr); }
  const box = $("gen-issues");
  box.replaceChildren();
  for (const i of data.issues) box.append(el("p", `${i.severity}: ${i.field}: ${i.message}`, i.severity === "error" ? "bad" : "warn"));
  if (!data.issues.length) box.append(el("p", "no issues", "muted"));
}

function loadDraft() {
  if (!genResult) return;
  selected = { name: "generated", kind: "draft", yaml: genResult.yaml, read_only: false };
  $("cfg-title").textContent = "draft (unsaved): generated";
  $("cfg-body").hidden = false; $("btn-copy").hidden = true; $("btn-delete").hidden = true;
  buildForm(genResult.values);
  setEditing(true);
  $("cfg-newname").value = "generated";
  for (const tr of $("cfg-list").tBodies[0].rows) tr.classList.remove("sel");
  renderView(genResult.report, genResult.yaml);
  $("cfg-title").scrollIntoView({ block: "nearest" });
}

export function initConfigs() {
  $("btn-copy").addEventListener("click", () => {
    selected = { ...selected, kind: "draft", read_only: false, name: `${selected.name}_copy` };
    setEditing(true);
    $("cfg-newname").value = selected.name;
    $("cfg-title").textContent = `draft (unsaved): ${selected.name}`;
    $("btn-copy").hidden = true; $("btn-delete").hidden = true;
    badges(lastReport, "draft (unsaved)");
    $("btn-save").disabled = !lastReport.valid && !$("cfg-anyway").checked;
    $("anyway-wrap").hidden = lastReport.valid;
  });
  $("btn-targets").addEventListener("click", () => {
    const d = lastReport && lastReport.timeline && lastReport.timeline.derived;
    if (!d) return;
    $("gen-rr").value = +d.range_resolution_m.toFixed(3); $("gen-rmax").value = Math.round(d.max_unambiguous_range_m);
    $("gen-vmax").value = +d.max_unambiguous_velocity_mps.toFixed(3);
    $("gen-nchirps").value = Math.round(lastReport.timeline.chirps_per_dwell);
    $("gen-msg").textContent = `targets from ${selected.name}`;
  });
  $("cfg-anyway").addEventListener("change", () => { $("btn-save").disabled = !editing || (!lastReport.valid && !$("cfg-anyway").checked); });
  $("btn-save").addEventListener("click", save);
  $("btn-cancel").addEventListener("click", () => { selected = null; $("cfg-body").hidden = true; $("cfg-title").textContent = "Select a config"; $("cfg-badges").replaceChildren(); const f = $("cfg-list").tBodies[0].rows[0]; if (f) f.click(); });
  $("btn-delete").addEventListener("click", del);
  $("btn-gen").addEventListener("click", generate);
  $("btn-gen-load").addEventListener("click", loadDraft);
  loadList().then(() => { const first = $("cfg-list").tBodies[0].rows[0]; if (first) first.click(); });
}
