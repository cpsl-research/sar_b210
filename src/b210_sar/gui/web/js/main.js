import { connectStream } from "./stream.js";
import { initConfigs } from "./configs.js";
import { initFileView, onMessage as fileMessage } from "./fileview.js";

const status = document.getElementById("status");
let connected = false, last = "";
function render() {
  status.textContent = connected ? (last || "connected") : "disconnected";
  document.getElementById("statusdot").className = "dot " + (connected ? "ok" : "bad");
}
connectStream((m) => { fileMessage(m); if (m.type === "status") { last = `source ${m.source}: ${m.msg || m.state}`; render(); } },
              (up) => { connected = up; render(); });

const tabBtns = [...document.querySelectorAll("#tabs button")];
function showTab(name, setHash) {
  const b = tabBtns.find((x) => x.dataset.tab === name && !x.disabled);
  if (!b) return;
  for (const o of tabBtns) { o.classList.toggle("on", o === b); o.setAttribute("aria-selected", o === b ? "true" : "false"); }
  for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== `tab-${name}`;
  if (setHash && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
}
for (const b of tabBtns) b.addEventListener("click", () => showTab(b.dataset.tab, true));
window.addEventListener("hashchange", () => showTab(location.hash.slice(1), false));  // deep links: /#configs, /#data
showTab(location.hash.slice(1) || "data", false);
initConfigs();
initFileView();
