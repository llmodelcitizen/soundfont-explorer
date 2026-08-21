import { chromium } from 'playwright';
const url = process.argv[2] ?? 'https://soundfonts.ericq.com/';
const out = process.argv[3] ?? '../work/shots';
const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
for (const theme of ['modern', 'win95']) {
  const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
  await p.goto(`${url}?theme=${theme}`, { waitUntil: 'networkidle' });
  await p.waitForSelector('.rows .row', { timeout: 20000 });
  await p.keyboard.press(' ');
  await p.waitForFunction(() => document.querySelector('.row.audible') !== null, null, { timeout: 20000 });
  await p.keyboard.press('f');
  await p.keyboard.press('ArrowDown');
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `${out}/${theme}-main.png` });
  await p.keyboard.press('d');
  await p.keyboard.press('?');
  await p.waitForTimeout(300);
  await p.screenshot({ path: `${out}/${theme}-overlays.png` });
  await p.close();
}
const m = await b.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
await m.goto(url, { waitUntil: 'networkidle' });
await m.waitForSelector('.rows .row', { timeout: 20000 });
  await m.tap('.rows .row:nth-child(3)');
await m.waitForFunction(() => document.querySelector('.row.audible') !== null, null, { timeout: 20000 });
await m.waitForTimeout(1000);
await m.screenshot({ path: `${out}/mobile.png` });
await b.close();
