// Minimal headless-Chrome driver over the DevTools protocol (Node 22+: built-in WebSocket, no npm packages).
// Used by tests/test_dashboard_browser.py and the UI-review screenshots. Every run uses a fresh scratch profile.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

export async function launch({ width = 1440, height = 900, dark = false } = {}) {
  const profile = mkdtempSync(join(tmpdir(), "hs-chrome-"));
  const proc = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
    "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars", `--window-size=${width},${height}`, "about:blank"],
  { stdio: ["ignore", "ignore", "pipe"] });
  const wsUrl = await new Promise((res, rej) => {
    let buf = ""; const t = setTimeout(() => rej(new Error("Chrome did not start")), 20000);
    proc.stderr.on("data", (d) => { buf += d; const m = /DevTools listening on (ws:\/\/\S+)/.exec(buf); if (m) { clearTimeout(t); res(m[1]); } });
  });
  const port = new URL(wsUrl).port;
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const pageWs = targets.find((t) => t.type === "page").webSocketDebuggerUrl;
  const ws = new WebSocket(pageWs);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let id = 0; const pending = new Map(); const listeners = [];
  ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { const { res, rej } = pending.get(msg.id); pending.delete(msg.id); msg.error ? rej(new Error(msg.error.message)) : res(msg.result); }
    else for (const l of listeners) l(msg);
  });
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  const errors = []; const logs = [];
  listeners.push((m) => {
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception ? m.params.exceptionDetails.exception.description : m.params.exceptionDetails.text);
    if (m.method === "Runtime.consoleAPICalled") { const text = m.params.args.map((a) => a.value ?? a.description ?? "").join(" "); (m.params.type === "error" ? errors : logs).push(text); }
    if (m.method === "Log.entryAdded" && m.params.entry.level === "error") errors.push(`${m.params.entry.text} ${m.params.entry.url || ""}`.trim());
  });
  await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }] });
  const page = {
    send, errors, logs,
    async goto(url) {
      const loaded = new Promise((r) => { const f = (m) => { if (m.method === "Page.loadEventFired") { listeners.splice(listeners.indexOf(f), 1); r(); } }; listeners.push(f); });
      await send("Page.navigate", { url }); await loaded; await page.sleep(300);
    },
    async eval(expr) {
      const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error(`eval failed: ${r.exceptionDetails.exception ? r.exceptionDetails.exception.description : r.exceptionDetails.text}\n${expr.slice(0, 200)}`);
      return r.result.value;
    },
    async key(key, opts = {}) {
      const map = { Tab: [9, "Tab"], Enter: [13, "Enter"], Escape: [27, "Escape"], " ": [32, "Space"], ArrowDown: [40, "ArrowDown"], ArrowUp: [38, "ArrowUp"] };
      const [code, codeName] = map[key] || [key.toUpperCase().charCodeAt(0), `Key${key.toUpperCase()}`];
      const mods = (opts.shift ? 8 : 0) | (opts.meta ? 4 : 0);
      const base = { key, code: codeName, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code, modifiers: mods };
      await send("Input.dispatchKeyEvent", { type: "keyDown", ...base, text: key.length === 1 ? key : undefined });
      await send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
      await page.sleep(60);
    },
    async click(selector) {
      const box = await page.eval(`(() => { const n = document.querySelector(${JSON.stringify(selector)}); if (!n) return null; n.scrollIntoView({block: "center"}); const r = n.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
      if (!box) throw new Error(`no element ${selector}`);
      for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x: box[0], y: box[1], button: "left", clickCount: 1 });
      await page.sleep(120);
    },
    async screenshot(path, full = true) {
      let clip;
      if (full) { const m = await send("Page.getLayoutMetrics"); clip = { x: 0, y: 0, width: m.cssContentSize.width, height: Math.min(m.cssContentSize.height, 6000), scale: 1 }; }
      const r = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: !!full, clip });
      writeFileSync(path, Buffer.from(r.data, "base64"));
    },
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    async close() { try { ws.close(); } catch (e) { /* gone */ } proc.kill("SIGKILL"); try { rmSync(profile, { recursive: true, force: true }); } catch (e) { /* busy */ } },
  };
  return page;
}
