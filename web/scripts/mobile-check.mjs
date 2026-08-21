import { chromium, devices } from 'playwright';
const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
const ctx = await b.newContext({ ...devices['iPhone 13'], locale: 'en-US' });
const p = await ctx.newPage();
const errors = [];
p.on('pageerror', (e) => errors.push(String(e)));
p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
await p.goto('https://soundfonts.ericq.com/', { waitUntil: 'networkidle' });
await p.waitForSelector('.rows .row', { timeout: 20000 });
  await p.tap('.rows .row:nth-child(3)');
await p.waitForFunction(() => document.querySelector('.row.audible') !== null, null, { timeout: 20000 });
const layout = await p.evaluate(() => {
  const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height), visible: b.width > 0 && b.height > 0 && getComputedStyle(e).display !== 'none' }; };
  return { vw: innerWidth, vh: innerHeight, scrollW: document.documentElement.scrollWidth, list: r('.list'), steps: r('.transport .steps'), stepBtn: r('.transport .steps .btn'), np: r('.nowplaying'), transport: r('.transport'), meta: r('.row .meta') };
});
// press ▼ three times via touch
const sel0 = await p.$eval('.row.sel', (e) => e.dataset.index);
for (let i = 0; i < 3; i++) { await p.tap('.transport .steps .btn:nth-child(2)'); await p.waitForTimeout(300); }
await p.waitForTimeout(800);
const sel1 = await p.$eval('.row.sel', (e) => e.dataset.index);
const aud = await p.$eval('.row.audible', (e) => e.dataset.index);
console.log(JSON.stringify({ layout, sel0, sel1, aud, errors }, null, 1));
await b.close();
