import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
const url = process.argv[2] ?? 'https://soundfonts.ericq.com/';
const out = process.argv[3] ?? '../work/shots';
await mkdir(out, { recursive: true });
const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
for (const theme of ['modern', 'win95', 'amiga']) {
  const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
  await p.goto(`${url}?theme=${theme}`, { waitUntil: 'networkidle' });
  await p.waitForSelector('.rows .row', { timeout: 20000 });
  await p.evaluate(async () => {
    await Promise.all([document.fonts.load('14px "Matrix Hyperpix"', 'Matrix'), document.fonts.load('11px "Pixelated MS Sans Serif"'), document.fonts.load('16px "Fixedsys Excelsior"')]);
    await document.fonts.ready;
  });
  await p.keyboard.press(' ');
  await p.waitForFunction(() => document.querySelector('.row.audible') !== null, null, { timeout: 20000 });
  await p.keyboard.press('ArrowDown');
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `${out}/${theme}-main.png` });
  await p.keyboard.press('f');
  await p.screenshot({ path: `${out}/${theme}-filters.png` });
  await p.keyboard.press('f');
  await p.keyboard.press('?');
  await p.waitForTimeout(200);
  await p.screenshot({ path: `${out}/${theme}-overlays.png` });
  await p.keyboard.press('?');
  await p.keyboard.press('s');
  await p.waitForTimeout(200);
  await p.screenshot({ path: `${out}/${theme}-settings.png` });
  await p.keyboard.press('s');
  await p.keyboard.press('d');
  const head = p.locator('.dbg-head');
  const box = await head.boundingBox();
  if (box) {
    await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await p.mouse.down();
    await p.mouse.move(180, 170, { steps: 5 });
    await p.mouse.up();
  }
  await p.screenshot({ path: `${out}/${theme}-debug.png` });
  if (theme === 'win95') {
    const creditsUrl = new URL(url);
    creditsUrl.searchParams.set('theme', 'win95');
    creditsUrl.hash = '/credits';
    await p.goto(creditsUrl.href, { waitUntil: 'networkidle' });
    await p.evaluate(async () => {
      await Promise.all([document.fonts.load('14px "Matrix Hyperpix"', 'Matrix'), document.fonts.load('11px "Pixelated MS Sans Serif"'), document.fonts.load('16px "Fixedsys Excelsior"')]);
      await document.fonts.ready;
    });
    await p.screenshot({ path: `${out}/win95-about.png`, fullPage: true });
  }
  await p.close();
}
for (const theme of ['modern', 'win95', 'amiga']) {
  const m = await b.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
  await m.goto(`${url}?theme=${theme}`, { waitUntil: 'networkidle' });
  await m.waitForSelector('.rows .row', { timeout: 20000 });
  await m.evaluate(async () => {
    await Promise.all([document.fonts.load('14px "Matrix Hyperpix"', 'Matrix'), document.fonts.load('11px "Pixelated MS Sans Serif"'), document.fonts.load('16px "Fixedsys Excelsior"')]);
    await document.fonts.ready;
  });
  await m.tap('.rows .row:nth-child(3)');
  await m.waitForFunction(() => document.querySelector('.row.audible') !== null, null, { timeout: 20000 });
  await m.waitForTimeout(1000);
  const filename = theme === 'modern' ? 'mobile.png' : `${theme}-mobile.png`;
  await m.screenshot({ path: `${out}/${filename}` });
  await m.close();
}
await b.close();
