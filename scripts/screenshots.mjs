// Take the README screenshots from the demo UI (scripts/demo.py).
//
//   .venv/bin/python scripts/demo.py 8095 &
//   google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir="$(mktemp -d)" &
//   node scripts/screenshots.mjs http://127.0.0.1:8095 docs/screenshots
//
// Needs Node 22+ (built-in fetch and WebSocket) and Chrome/Chromium with remote debugging on
// port 9333 (override with CDP=http://host:port). The last shot logs the demo out.
import { mkdirSync, writeFileSync } from "node:fs";

const [base = "http://127.0.0.1:8095", out = "docs/screenshots"] = process.argv.slice(2);
const CDP = process.env.CDP || "http://127.0.0.1:9333";
mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function tab() {
  const t = await (await fetch(`${CDP}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const pending = {};
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (pending[d.id]) { pending[d.id](d.result ?? {}); delete pending[d.id]; }
  };
  const cmd = (method, params = {}) =>
    new Promise((r) => { pending[++id] = r; ws.send(JSON.stringify({ id, method, params })); });
  const js = async (expression) =>
    (await cmd("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result?.value;
  return { cmd, js, close: () => fetch(`${CDP}/json/close/${t.id}`) };
}

// Open `path` at the given size/theme, run `prepare` (page JS or a function of the tab),
// then save a PNG of the page, cropped to `height` if given or else the full page.
async function shot(name, path, { width = 1200, dark = false, mobile = false, height, prepare } = {}) {
  const t = await tab();
  await t.cmd("Emulation.setDeviceMetricsOverride",
    { width, height: height || 900, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await t.cmd("Emulation.setEmulatedMedia",
    { features: [{ name: "prefers-color-scheme", value: dark ? "dark" : "light" }] });
  await t.cmd("Page.enable");
  await t.cmd("Page.navigate", { url: base + path });
  await sleep(1200);
  // Load lazy images right away so none are blank in the shot.
  await t.js(`document.querySelectorAll('img[loading]').forEach(i => i.removeAttribute('loading'))`);
  if (typeof prepare === "string") await t.js(prepare);
  else if (prepare) await prepare(t);
  await sleep(500);
  const h = height || Math.ceil(await t.js("document.documentElement.scrollHeight"));
  const { data } = await t.cmd("Page.captureScreenshot",
    { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width, height: h, scale: 1 } });
  writeFileSync(`${out}/${name}.png`, Buffer.from(data, "base64"));
  console.log(`${out}/${name}.png`);
  await t.close();
}

// A row of the Points Channels table, held mid-drag (pointer pressed and moved, not released).
async function midDrag(t) {
  const [x, y, ty] = await t.js(`(() => {
    const h = document.querySelectorAll('#channel-rows .handle');
    const a = h[6].getBoundingClientRect(), b = h[1].getBoundingClientRect();
    return [a.x + a.width / 2, a.y + a.height / 2, b.y + b.height / 2];
  })()`);
  const mouse = (type, yy) => t.cmd("Input.dispatchMouseEvent",
    { type, x, y: yy, button: "left", buttons: 1, clickCount: 1 });
  await mouse("mousePressed", y);
  for (let i = 1; i <= 8; i++) await mouse("mouseMoved", y + ((ty - y) * i) / 8);
}

await shot("dashboard", "/");
await shot("dashboard-dark", "/", { dark: true });
await shot("campaigns", "/campaigns", { height: 1005 });
await shot("campaigns-search", "/campaigns?q=skin", { height: 640 });
await shot("channels", "/channels?check=starlitsam");
await shot("channels-drag", "/channels", { height: 760, prepare: midDrag });
await shot("mobile", "/", { width: 400, mobile: true, height: 860 });
await shot("login", "/login", {
  height: 560,
  prepare: async (t) => {
    await t.js(`fetch('/logout', { method: 'POST' })`);
    await t.cmd("Page.navigate", { url: base + "/login" });
    await sleep(1000);
  },
});
