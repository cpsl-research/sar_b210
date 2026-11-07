// Thin fetch helpers. Every call returns {status, data}; nothing throws on HTTP errors.
export async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch (e) { /* no body */ }
  return { status: r.status, data };
}
