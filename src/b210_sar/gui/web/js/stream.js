// WebSocket /stream: status messages now, frames from later steps. Reconnects every 2 s.
export function connectStream(onMessage, onState) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  function open() {
    const ws = new WebSocket(`${proto}://${location.host}/stream`);
    ws.onopen = () => onState(true);
    ws.onmessage = (ev) => { try { onMessage(JSON.parse(ev.data)); } catch (e) { /* ignore */ } };
    ws.onclose = () => { onState(false); setTimeout(open, 2000); };
  }
  open();
}
