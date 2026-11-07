import { api } from "./api.js";

const $ = (id) => document.getElementById(id);
const FIELDS = [
  ["waveform.num_steps", "num_steps", ""], ["waveform.step_spacing_hz", "step_spacing_hz", "Hz"],
  ["waveform.chirp_bandwidth_hz", "chirp_bandwidth_hz", "Hz"], ["waveform.chirp_length_s", "chirp_length_s", "s"],
  ["waveform.dwell_s", "dwell_s", "s"], ["waveform.taper_fraction", "taper_fraction", ""],
  ["waveform.sample_rate_hz", "sample_rate_hz", "Hz"], ["rf.rx_gain_db", "rx_gain_db", "dB"],
  ["rf.tx_gain_db", "tx_gain_db", "dB"],
];
let selected = null;   // {name, kind, yaml, read_only}
let editing = false;
let draftYaml = "";

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
  badges(rep, selected.kind);
  showIssues(rep); showDerived(rep);
  $("cfg-yaml").textContent = text;
  $("ro-note").textContent = selected.read_only ? "(shipped: never written; copy to edit)" : "";
}

async function select(name, kind) {
  const { status, data } = await api(`/api/configs/${encodeURIComponent(name)}?kind=${kind}`);
  if (status !== 200) { $("cfg-title").textContent = data.error; return; }
  selected = { name: data.name, kind: data.kind, yaml: data.yaml, read_only: data.read_only };
  editing = false;
  $("cfg-title").textContent = data.name;
  $("cfg-body").hidden = false; $("cfg-form").hidden = true;
  $("btn-copy").hidden = false; $("btn-delete").hidden = data.read_only;
  renderView(data.report, data.yaml);
  for (const tr of $("cfg-list").tBodies[0].rows) tr.classList.toggle("sel", tr.dataset.name === name && tr.dataset.kind === kind);
}

function buildForm(values) {
  const box = $("cfg-fields");
  box.replaceChildren();
  for (const [key, label, unit] of FIELDS) {
    const l = el("label"); l.append(el("span", `${label} ${unit}`.trim()));
    const i = el("input"); i.name = key; i.value = values[key] ?? ""; i.inputMode = "decimal";
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

async function check() {
  let fields;
  try { fields = formFields(); } catch (e) { $("cfg-issues").replaceChildren(el("p", e.message, "bad")); return null; }
  const { data } = await api("/api/configs/check", { method: "POST", body: { yaml: selected.yaml, fields } });
  if (!data.ok) { $("cfg-issues").replaceChildren(el("p", data.error, "bad")); return null; }
  draftYaml = data.yaml;
  renderView(data.report, data.yaml);
  badges(data.report, "user (draft)");
  return { data, fields };
}

async function save() {
  const name = $("cfg-newname").value.trim();
  if (!name) { $("cfg-issues").replaceChildren(el("p", "enter a name to save as", "bad")); return; }
  const c = await check();
  if (!c) return;
  const { status, data } = await api("/api/configs", { method: "POST", body: { name, yaml: selected.yaml, fields: c.fields } });
  if (status !== 201) { $("cfg-issues").append(el("p", `not saved: ${data.error}`, "bad")); return; }
  await loadList();
  await select(data.name, "user");
  $("cfg-issues").prepend(el("p", `saved as ${data.name}`, "ok"));
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

export function initConfigs() {
  $("btn-copy").addEventListener("click", async () => {
    const { data } = await api(`/api/configs/${encodeURIComponent(selected.name)}?kind=${selected.kind}`);
    editing = true;
    $("cfg-form").hidden = false;
    $("cfg-newname").value = `${selected.name}_copy`;
    buildForm(data.values);
  });
  $("btn-check").addEventListener("click", check);
  $("btn-save").addEventListener("click", save);
  $("btn-cancel").addEventListener("click", () => select(selected.name, selected.kind));
  $("btn-delete").addEventListener("click", del);
  loadList().then(() => { const first = $("cfg-list").tBodies[0].rows[0]; if (first) first.click(); });
}
