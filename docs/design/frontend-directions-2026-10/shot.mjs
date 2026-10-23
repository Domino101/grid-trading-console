
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';

const [url, out, w = '1600', h = '1000', scale = '1', mobile = 'false'] = process.argv.slice(2);
const port = 9333 + Math.floor(Math.random() * 200);
const chrome = spawn('google-chrome', [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars',
  '--user-data-dir=/tmp/chrome-shot-' + port,
  '--remote-debugging-port=' + port, 'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let ver;
for (let i = 0; i < 50; i++) {
  try { ver = await (await fetch('http://127.0.0.1:' + port + '/json/version')).json(); break; }
  catch { await sleep(150); }
}
if (!ver) { console.error('chrome did not start'); chrome.kill(); process.exit(1); }

const target = await (await fetch('http://127.0.0.1:' + port + '/json/new?about:blank', { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let id = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
};
const send = (method, params = {}) => new Promise((res) => {
  const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params }));
});

await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: +w, height: +h, deviceScaleFactor: +scale, mobile: mobile === 'true' });
await send('Page.navigate', { url });
await sleep(1600);
const m = await send('Runtime.evaluate', { expression: "JSON.stringify({vh:innerHeight,sw:document.documentElement.scrollWidth,sh:document.documentElement.scrollHeight,scroll:(document.querySelector('.scroll')||{}).scrollHeight,tbl:(()=>{const t=document.querySelector('.tablepanel table')||document.querySelector('.list table');const p=document.querySelector('.tablepanel')||document.querySelector('.list');if(!t)return null;return {t:Math.round(t.getBoundingClientRect().height),pb:Math.round(p.getBoundingClientRect().bottom),tb:Math.round(t.getBoundingClientRect().bottom),pc:p.clientHeight,ps:p.scrollHeight}})(),last:(()=>{const s=document.querySelector('.scroll');const c=s&&s.lastElementChild;const r=c&&c.getBoundingClientRect();return r?{top:Math.round(r.top),bottom:Math.round(r.bottom),h:Math.round(r.height)}:null})()})", returnByValue: true });
console.log('metrics', m.result && m.result.result && m.result.result.value);
const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false, fromSurface: true });
writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
console.log('saved', out, w + 'x' + h, 'bytes', shot.result.data.length);
ws.close();
chrome.kill();
process.exit(0);
