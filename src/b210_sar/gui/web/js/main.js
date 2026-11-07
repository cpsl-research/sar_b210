import { connectStream } from "./stream.js";
import { initConfigs } from "./configs.js";
import { initFileView, onMessage as fileMessage } from "./fileview.js";

const status = document.getElementById("status");
let connected = false, last = "";
function render() {
  status.textContent = connected ? (last || "connected") : "disconnected";
  status.className = connected ? "muted" : "bad";
}
connectStream((m) => { fileMessage(m); if (m.type === "status") { last = `source ${m.source}: ${m.msg || m.state}`; render(); } },
              (up) => { connected = up; render(); });

for (const b of document.querySelectorAll("#tabs button")) {
  b.addEventListener("click", () => {
    if (b.disabled) return;
    for (const o of document.querySelectorAll("#tabs button")) o.classList.toggle("active", o === b);
    for (const s of document.querySelectorAll("main > section")) s.hidden = s.id !== `tab-${b.dataset.tab}`;
  });
}
initConfigs();
initFileView();
