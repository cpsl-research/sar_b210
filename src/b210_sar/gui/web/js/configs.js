import { api } from "./api.js";
import { drawWaveform } from "./waveform.js";

const $ = (id) => document.getElementById(id);
let table = null;   // GET /api/configs/fields: {fields, readonly, groups}; the only field list
let selected = null;   // {name, kind: shipped|user|draft, yaml, read_only}
let editing = false;
let mode = "edit";     // edit | generate (only while editing a draft)
const touched = new Set();
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
  showBand(rep); markIssues(rep); sampleHint();
  $("cfg-yaml").textContent = text;
  const bad = !rep.valid;
  $("anyway-wrap").hidden = !bad || !editing;
  if (!bad) $("cfg-anyway").checked = false;
  $("btn-save").disabled = !editing || (bad && !$("cfg-anyway").checked);
}

function setEditing(on) {
  editing = on;
  lockForm();
  $("cfg-saveline").hidden = !on;
  $("cfg-mode").hidden = !on;
  $("ro-note").textContent = on ? "(editing a draft; shipped files are never written)" : selected.read_only ? "(shipped: read-only; press New config to edit a copy)" : "(saved config: press New config to edit a copy)";
  if (!on) setMode("edit");
}

function lockForm() {
  for (const i of $("cfg-fields").querySelectorAll("input,select")) i.disabled = !editing || mode !== "edit" || i.closest("label").classList.contains("ro");
}

function setMode(m) {
  mode = m;
  $("btn-mode-edit").classList.toggle("on", m === "edit"); $("btn-mode-gen").classList.toggle("on", m === "generate");
  $("gen-card").hidden = !(editing && m === "generate");
  lockForm();
}

async function select(name, kind) {
  const { status, data } = await api(`/api/configs/${encodeURIComponent(name)}?kind=${kind}`);
  if (status !== 200) { $("cfg-title").textContent = data.error; return; }
  selected = { name: data.name, kind: data.kind, yaml: data.yaml, read_only: data.read_only };
  $("cfg-title").textContent = data.name;
  $("cfg-body").hidden = false;
  $("btn-new").hidden = false; $("btn-delete").hidden = data.read_only;
  buildForm(data.display);
  setEditing(false);
  renderView(data.report, data.yaml);
  for (const tr of $("cfg-list").tBodies[0].rows) tr.classList.toggle("sel", tr.dataset.name === name && tr.dataset.kind === kind);
}

function buildForm(display) {
  const box = $("cfg-fields");
  box.replaceChildren();
  touched.clear();
  if (!table) return;
  const rows = [...table.fields, ...table.readonly.map((f) => ({ ...f, ro: true }))];
  for (const g of table.groups) {
    const det = el("details"); det.open = true; det.dataset.group = g;
    det.append(el("summary", g));
    const grid = el("div", undefined, "grp");
    for (const f of rows.filter((r) => r.group === g)) {
      const l = el("label", undefined, f.ro ? "ro" : ""); l.dataset.key = f.key; l.title = f.hint || "";
      l.append(el("span", f.label));
      const fr = el("span", undefined, "fr");
      let i;
      if (f.kind === "enum") {
        i = el("select");
        for (const o of f.options) { const op = el("option", o); op.value = o; i.append(op); }
        i.value = display[f.key] ?? f.options[0];
      } else {
        i = el("input"); i.value = display[f.key] ?? ""; i.inputMode = f.kind === "int" ? "numeric" : "decimal";
      }
      i.name = f.key; i.dataset.orig = i.value; i.disabled = true;
      const ev = i.tagName === "SELECT" ? "change" : "input";
      if (!f.ro) i.addEventListener(ev, () => { touch(f.key); clearTimeout(timer); timer = setTimeout(check, 150); });
      const mk = el("span", "", "mk");
      fr.append(mk, i, el("span", f.unit, "fu"));
      l.append(fr);
      if (f.key === "waveform.chirp_length_s") { const h = el("span", "", "fh"); h.id = "hint-samples"; l.append(h); }
      else if (f.hint) l.append(el("span", f.hint, "fh"));
      grid.append(l);
    }
    det.append(grid); box.append(det);
  }
  lockForm();
}

const input = (k) => $("cfg-fields").querySelector(`[name="${k}"]`);

// dwell and chirps-per-dwell are two views of one quantity: the last one touched wins
function touch(k) {
  touched.add(k);
  if (k === "waveform.dwell_s") touched.delete("waveform.chirps_per_dwell");
  if (k === "waveform.chirps_per_dwell") touched.delete("waveform.dwell_s");
}

// Only edited fields are sent, as the text typed, in display units: the server converts by decimal shift. (Echoing
// every displayed value back would round whole-sample quantities such as chirp_length_s and break them.)
function formFields() {
  const f = {};
  for (const k of touched) {
    const i = input(k), v = i.value.trim();
    if (i.tagName !== "SELECT" && (v === "" || !Number.isFinite(Number(v)))) throw new Error(`${k}: enter a number`);
    f[k] = v;
  }
  // a new chirp length keeps the number of chirps per dwell (the dwell follows), unless the dwell itself was edited
  if (touched.has("waveform.chirp_length_s") && !touched.has("waveform.dwell_s") && !touched.has("waveform.chirps_per_dwell")) {
    const n = input("waveform.chirps_per_dwell").value.trim();
    if (/^\d+$/.test(n)) f["waveform.chirps_per_dwell"] = n;
  }
  return f;
}

// the linked fields not being typed in follow the server's answer
function syncLinked(display) {
  for (const k of ["waveform.dwell_s", "waveform.chirps_per_dwell"]) if (!touched.has(k) && display[k] !== undefined) input(k).value = display[k];
}

function sampleHint() {
  const h = $("hint-samples"), l = input("waveform.chirp_length_s"), r = input("waveform.sample_rate_hz");
  if (!h || !l || !r) return;
  const n = Number(l.value) * Number(r.value);   // us * MS/s
  if (!Number.isFinite(n) || n <= 0) { h.textContent = ""; return; }
  const whole = Math.abs(n - Math.round(n)) < 1e-6;
  h.textContent = whole ? `= ${Math.round(n)} samples` : `= ${+n.toFixed(4)} samples (not a whole number)`;
  h.className = `fh ${whole ? "" : "bad"}`;
}

function showBand(rep) {
  const t = rep.timeline;
  $("cfg-band").textContent = t ? `start / stop: ${+(t.band_lo_hz / 1e6).toFixed(4)} / ${+(t.band_hi_hz / 1e6).toFixed(4)} MHz (stitched band ${+(t.total_band_hz / 1e6).toFixed(4)} MHz, ${t.num_steps} step${t.num_steps === 1 ? "" : "s"})` : "";
}

function markIssues(rep) {
  for (const l of $("cfg-fields").querySelectorAll("label")) { l.classList.remove("err", "warn"); l.querySelector(".mk").textContent = ""; l.querySelector(".mk").title = ""; delete l.dataset.issue; }
  for (const [sev, list] of [["warn", rep.warnings], ["err", rep.errors]]) {
    for (const i of list) {
      const l = $("cfg-fields").querySelector(`label[data-key="${i.field}"]`);
      if (!l) continue;
      l.classList.remove("err", "warn"); l.classList.add(sev); l.dataset.issue = sev;
      const mk = l.querySelector(".mk"); mk.textContent = "!"; mk.title = i.message;
    }
  }
}

// Live recompute: every edit (debounced 150 ms) re-checks the draft and redraws the diagram.
async function check() {
  let fields;
  try { fields = formFields(); } catch (e) { $("cfg-issues").replaceChildren(el("p", e.message, "bad")); $("btn-save").disabled = true; return null; }
  const { data } = await api("/api/configs/check", { method: "POST", body: { yaml: selected.yaml, display: fields } });
  if (!data || !data.ok) { $("cfg-issues").replaceChildren(el("p", data ? data.error : "check failed", "bad")); $("btn-save").disabled = true; return null; }
  syncLinked(data.display);
  renderView(data.report, data.yaml);
  return { data, fields };
}

async function save() {
  const name = $("cfg-newname").value.trim();
  if (!name) { $("cfg-issues").replaceChildren(el("p", "enter a name to save as", "bad")); return; }
  const c = await check();
  if (!c) return;
  const anyway = !c.data.report.valid && $("cfg-anyway").checked;
  const { status, data } = await api("/api/configs", { method: "POST", body: { name, yaml: selected.yaml, display: c.fields, allow_invalid: anyway } });
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
  $("cfg-body").hidden = false; $("btn-new").hidden = true; $("btn-delete").hidden = true;
  buildForm(genResult.values);
  setEditing(true);
  $("cfg-newname").value = "generated";
  for (const tr of $("cfg-list").tBodies[0].rows) tr.classList.remove("sel");
  renderView(genResult.report, genResult.yaml);
  $("cfg-title").scrollIntoView({ block: "nearest" });
}

export function initConfigs() {
  $("btn-new").addEventListener("click", () => {
    if (!selected) return;
    selected = { ...selected, kind: "draft", read_only: false, name: `${selected.name.replace(/_new\d*$/, "")}_new` };
    setEditing(true); setMode("edit");
    $("cfg-newname").value = selected.name;
    $("cfg-title").textContent = `draft (unsaved): ${selected.name}`;
    $("btn-new").hidden = true; $("btn-delete").hidden = true;
    $("gen-base").value = [...$("gen-base").options].some((o) => o.value === selected.name.replace(/_new$/, "")) ? selected.name.replace(/_new$/, "") : $("gen-base").value;
    badges(lastReport, "draft (unsaved)");
    $("btn-save").disabled = !lastReport.valid && !$("cfg-anyway").checked;
    $("anyway-wrap").hidden = lastReport.valid;
  });
  $("btn-mode-edit").addEventListener("click", () => setMode("edit"));
  $("btn-mode-gen").addEventListener("click", () => setMode("generate"));
  $("btn-targets").addEventListener("click", () => {
    const d = lastReport && lastReport.timeline && lastReport.timeline.derived;
    if (!d) return;
    $("gen-rr").value = +d.range_resolution_m.toFixed(3); $("gen-rmax").value = Math.round(d.max_unambiguous_range_m);
    $("gen-vmax").value = +d.max_unambiguous_velocity_mps.toFixed(3);
    $("gen-nchirps").value = Math.round(lastReport.timeline.chirps_per_dwell);
    $("gen-msg").textContent = `targets from ${selected.name}`;
    if (!editing) $("btn-new").click();
    setMode("generate");
  });
  $("cfg-anyway").addEventListener("change", () => { $("btn-save").disabled = !editing || (!lastReport.valid && !$("cfg-anyway").checked); });
  $("btn-save").addEventListener("click", save);
  $("btn-cancel").addEventListener("click", () => { selected = null; $("cfg-body").hidden = true; $("cfg-title").textContent = "Select a config"; $("cfg-badges").replaceChildren(); const f = $("cfg-list").tBodies[0].rows[0]; if (f) f.click(); });
  $("btn-delete").addEventListener("click", del);
  $("btn-gen").addEventListener("click", generate);
  $("btn-gen-load").addEventListener("click", loadDraft);
  api("/api/configs/fields").then(({ data }) => { table = data; loadList().then(() => { const first = $("cfg-list").tBodies[0].rows[0]; if (first) first.click(); }); });
}
