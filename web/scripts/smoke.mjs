#!/usr/bin/env node
/**
 * Playwright smoke (plan M6 / §14.3): Space starts playback → hold ↓ (30 presses at 33 ms) → audible === end point,
 * warm switch p95 < 20 ms scheduled, no console errors, three-theme cycle, persistence, deep link.
 *
 *   node scripts/smoke.mjs [--url http://127.0.0.1:5173/] [--headed]
 * Requires: a server for out/public on :8000 (python3 web/test/proto/serve.py --root out/public --port 8000)
 * and `npx vite` on :5173 (or a built dist served with the same proxying).
 */
import { chromium } from 'playwright';

const args = Object.fromEntries(process.argv.slice(2).map((a) => a.replace(/^--/, '').split('=')));
const url = args.url ?? 'http://127.0.0.1:5173/';
const headed = 'headed' in args;

const browser = await chromium.launch({ headless: !headed, args: ['--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream'] });
const page = await browser.newPage();
const errors = [];
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text());
});
page.on('pageerror', (e) => errors.push(String(e)));

const results = { url, ok: true, steps: {} };
try {
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForSelector('.rows .row', { timeout: 20000 });
  await page.keyboard.press(' '); // there is no start gate: the first ▶ (Space) starts playback
  await page.waitForFunction(() => document.querySelector('.row.audible') !== null, null, { timeout: 20000 });
  results.steps.boot = { audible: await page.$eval('.row.audible .label', (e) => e.textContent), rows: await page.$$eval('.rows .row', (r) => r.length) };

  // open debug panel
  await page.keyboard.press('d');
  // hold ↓: 30 presses at 33 ms, repeat=true after the first
  await page.keyboard.press('Home');
  await page.waitForTimeout(600);
  const t0 = Date.now();
  await page.keyboard.down('ArrowDown');
  for (let i = 1; i < 30; i++) {
    await page.waitForTimeout(33);
    // synthesize repeats: Playwright's down() doesn't auto-repeat, so dispatch keydown with repeat=true
    await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', code: 'ArrowDown', repeat: true, bubbles: true })));
  }
  await page.keyboard.up('ArrowDown');
  const holdMs = Date.now() - t0;
  await page.waitForTimeout(1500);
  const sel = await page.$eval('.row.sel', (e) => e.dataset.index);
  const audibleIdx = await page.$eval('.row.audible', (e) => e.dataset.index).catch(() => null);
  results.steps.hold = { holdMs, cursor: Number(sel), audible: audibleIdx === null ? null : Number(audibleIdx), endsOnCursor: sel === audibleIdx };
  if (sel !== audibleIdx) results.ok = false;

  // warm switches: toggle between two cached rows 20 times, then read debug metrics
  for (let i = 0; i < 20; i++) {
    await page.keyboard.press(i % 2 ? 'ArrowDown' : 'ArrowUp');
    await page.waitForTimeout(150);
  }
  await page.waitForTimeout(500);
  const dbg = await page.$eval('.dbg-body', (e) => e.textContent);
  const m = /p50=([\d.]+) ms\s+p95=([\d.]+) ms/.exec(dbg ?? '');
  results.steps.latency = { p50: m ? Number(m[1]) : null, p95: m ? Number(m[2]) : null, debug: dbg?.split('\n').slice(0, 6) };
  if (!m || Number(m[2]) >= 20) results.ok = false;

  // status + position advancing
  const pos1 = await page.$eval('.clock', (e) => e.textContent);
  await page.waitForTimeout(1000);
  const pos2 = await page.$eval('.clock', (e) => e.textContent);
  results.steps.playing = { pos1, pos2, advancing: pos1 !== pos2 };
  if (pos1 === pos2) results.ok = false;

  // complete keyboard cycle, including dropdown + URL synchronization at every stop
  const cycle = [];
  for (const expected of ['win95', 'amiga', 'modern']) {
    await page.keyboard.press('t');
    await page.waitForTimeout(400); // URL sync is debounced (settleMs + 30)
    const state = await page.evaluate(() => ({
      root: document.documentElement.dataset.theme,
      dropdown: document.querySelector('.themepick')?.value,
      url: new URL(location.href).searchParams.get('theme') ?? 'modern',
    }));
    cycle.push(state);
    if (state.root !== expected || state.dropdown !== expected || state.url !== expected) results.ok = false;
  }

  // dropdown selection must update root, URL and persisted state
  await page.selectOption('.themepick', 'amiga');
  await page.waitForTimeout(400);
  const selected = await page.evaluate(() => ({
    root: document.documentElement.dataset.theme,
    dropdown: document.querySelector('.themepick')?.value,
    url: new URL(location.href).searchParams.get('theme'),
    stored: localStorage.getItem('sfp.theme'),
  }));
  results.steps.theme = { cycle, selected };
  if (selected.root !== 'amiga' || selected.dropdown !== 'amiga' || selected.url !== 'amiga' || selected.stored !== 'amiga') results.ok = false;
  const href = await page.evaluate(() => location.href);
  results.steps.url = href;

  // An explicit Amiga link wins over an old preference, and that choice survives a URL-free reload.
  const page2 = await browser.newPage();
  await page2.goto(url, { waitUntil: 'networkidle' });
  await page2.evaluate(() => localStorage.setItem('sfp.theme', 'modern'));
  const amigaLink = new URL(href.replace(/([?&])t=\d+/, '$1t=30'));
  amigaLink.searchParams.set('theme', 'amiga');
  await page2.goto(amigaLink.href, { waitUntil: 'networkidle' });
  await page2.waitForSelector('.rows .row', { timeout: 20000 });
  const theme2 = await page2.evaluate(() => document.documentElement.dataset.theme);
  await page2.keyboard.press(' ');
  await page2.waitForFunction(() => document.querySelector('.row.audible') !== null, null, { timeout: 20000 });
  const audible2 = await page2.$eval('.row.audible', (e) => e.dataset.id);
  const clock2 = await page2.$eval('.clock', (e) => e.textContent);
  results.steps.deeplink = { theme: theme2, audible: audible2, clock: clock2 };
  const wantV = new URL(href).searchParams.get('v');
  if (theme2 !== 'amiga' || audible2 !== wantV) results.ok = false;

  const cleanUrl = new URL(url);
  cleanUrl.searchParams.delete('theme');
  await page2.goto(cleanUrl.href, { waitUntil: 'networkidle' });
  await page2.waitForSelector('.rows .row', { timeout: 20000 });
  const persisted = await page2.evaluate(() => ({ root: document.documentElement.dataset.theme, dropdown: document.querySelector('.themepick')?.value, stored: localStorage.getItem('sfp.theme') }));
  results.steps.persistence = persisted;
  if (persisted.root !== 'amiga' || persisted.dropdown !== 'amiga' || persisted.stored !== 'amiga') results.ok = false;

  // The replacement Windows theme has the same deep-link and URL-free persistence contract.
  const win95Link = new URL(href);
  win95Link.searchParams.set('theme', 'win95');
  await page2.goto(win95Link.href, { waitUntil: 'networkidle' });
  await page2.waitForSelector('.rows .row', { timeout: 20000 });
  const win95DeepLink = await page2.evaluate(() => ({
    root: document.documentElement.dataset.theme,
    dropdown: document.querySelector('.themepick')?.value,
    stored: localStorage.getItem('sfp.theme'),
  }));
  results.steps.win95Deeplink = win95DeepLink;
  if (win95DeepLink.root !== 'win95' || win95DeepLink.dropdown !== 'win95' || win95DeepLink.stored !== 'win95') results.ok = false;

  await page2.goto(cleanUrl.href, { waitUntil: 'networkidle' });
  await page2.waitForSelector('.rows .row', { timeout: 20000 });
  const win95Persisted = await page2.evaluate(() => ({
    root: document.documentElement.dataset.theme,
    dropdown: document.querySelector('.themepick')?.value,
    stored: localStorage.getItem('sfp.theme'),
  }));
  results.steps.win95Persistence = win95Persisted;
  if (win95Persisted.root !== 'win95' || win95Persisted.dropdown !== 'win95' || win95Persisted.stored !== 'win95') results.ok = false;

  // credits page
  await page2.goto(new URL('/#/credits', url).href, { waitUntil: 'networkidle' });
  results.steps.credits = { h1: await page2.$eval('h1', (e) => e.textContent).catch(() => null) };
} catch (e) {
  results.ok = false;
  results.error = String(e);
}
results.consoleErrors = errors;
if (errors.length) results.ok = false;
console.log(JSON.stringify(results, null, 1));
await browser.close();
process.exit(results.ok ? 0 : 1);
