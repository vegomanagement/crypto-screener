// Minimal Chrome DevTools Protocol driver (no npm deps: Node ≥ 22 global WebSocket + fetch).
// Launches real Google Chrome headless, collects console errors / exceptions, evaluates and screenshots.
'use strict';
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function launch({ width = 1440, height = 900, scale = 2, port = 9333 } = {}) {
  const profile = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), 'scalp-chrome-'));
  const proc = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--no-first-run',
    '--no-default-browser-check', `--window-size=${width},${height}`, `--force-device-scale-factor=${scale}`, 'about:blank'], { stdio: 'ignore' });
  let targets;
  for (let k = 0; k < 50; k += 1) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (targets.some(t => t.type === 'page')) break; } catch (_) { /* booting */ }
    await sleep(200);
  }
  const target = targets.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map(), report = { console_errors: [], exceptions: [], failed_requests: [] };
  ws.onmessage = msg => {
    const data = JSON.parse(msg.data);
    if (data.id && pending.has(data.id)) { const { res, rej } = pending.get(data.id); pending.delete(data.id); data.error ? rej(new Error(data.error.message)) : res(data.result); return; }
    if (data.method === 'Runtime.consoleAPICalled' && data.params.type === 'error') report.console_errors.push(data.params.args.map(a => a.value ?? a.description).join(' '));
    if (data.method === 'Runtime.exceptionThrown') report.exceptions.push(data.params.exceptionDetails.exception?.description || data.params.exceptionDetails.text);
    if (data.method === 'Log.entryAdded' && data.params.entry.level === 'error') report.console_errors.push(`${data.params.entry.source}: ${data.params.entry.text} ${data.params.entry.url || ''}`);
    if (data.method === 'Network.responseReceived' && data.params.response.status >= 400) report.failed_requests.push(`${data.params.response.status} ${data.params.response.url}`);
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const k = ++id; pending.set(k, { res, rej }); ws.send(JSON.stringify({ id: k, method, params })); });
  await send('Runtime.enable'); await send('Log.enable'); await send('Network.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile: false });
  async function evaluate(expression) {
    const out = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (out.exceptionDetails) throw new Error(out.exceptionDetails.exception?.description || out.exceptionDetails.text);
    return out.result.value;
  }
  async function waitFor(expression, timeoutMs = 60000) {
    const end = Date.now() + timeoutMs;
    while (Date.now() < end) { if (await evaluate(`Boolean(${expression})`).catch(() => false)) return true; await sleep(250); }
    throw new Error(`timeout waiting for ${expression}`);
  }
  async function goto(url) { await send('Page.navigate', { url }); await sleep(500); }
  async function screenshot(file) { const { data } = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(file, Buffer.from(data, 'base64')); }
  async function close() { try { ws.close(); } catch (_) { /* gone */ } proc.kill(); await sleep(300); fs.rmSync(profile, { recursive: true, force: true }); }
  return { send, evaluate, waitFor, goto, screenshot, close, report, sleep };
}
module.exports = { launch, sleep };
