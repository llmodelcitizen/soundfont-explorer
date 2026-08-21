#!/usr/bin/env node
// Headless automation for the M0b prototype pages.
//   node run-probes.mjs                      # all three pages
//   node run-probes.mjs ab-switch --seconds=5 --out=../../../work/proto/results.json
// Starts serve.py on a free port, opens each page in headless Chromium with
// --autoplay-policy=no-user-gesture-required and ?autostart=1, waits for document.title === 'DONE',
// prints window.__results as JSON (one object keyed by page).
// Caveat: headless Chromium has no audio device; the AudioContext renders into a null sink with a real
// clock, so scheduling/drift/decode numbers are meaningful but nothing is audible and outputLatency is ~0.
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '..', '..', '..');
const argv = process.argv.slice(2);
const opts = Object.fromEntries(argv.filter(a => a.startsWith('--')).map(a => { const s = a.slice(2), i = s.indexOf('='); return i < 0 ? [s, '1'] : [s.slice(0, i), s.slice(i + 1)]; }));
const pages = argv.filter(a => !a.startsWith('--'));
const ALL = ['decode-probe', 'ab-switch', 'pack-fetch'];
const want = pages.length ? pages : ALL;
const timeoutMs = +(opts.timeout || 180000);

function freePort() {
  return new Promise((res, rej) => {
    const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); s.on('error', rej);
  });
}

const port = +(opts.port || await freePort());
const server = spawn('python3', [join(here, 'serve.py'), '--port', String(port), '--bind', '127.0.0.1'],
  { cwd: repoRoot, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, SERVE_QUIET: '1' } });
await new Promise((res, rej) => { server.stdout.once('data', res); server.once('exit', c => rej(new Error('serve.py exited ' + c))); });

const browser = await chromium.launch({
  headless: true,
  args: ['--autoplay-policy=no-user-gesture-required', '--disable-features=AudioServiceOutOfProcess'],
});
const results = { meta: { chromium: browser.version(), startedAt: new Date().toISOString(), port, headless: true } };
try {
  for (const name of want) {
    const q = new URLSearchParams({ autostart: '1' });
    for (const [k, v] of Object.entries(opts)) if (!['out', 'port', 'timeout'].includes(k)) q.set(k, v);
    const url = `http://127.0.0.1:${port}/web/test/proto/${name}.html?${q}`;
    const page = await browser.newPage();
    const consoleErrors = [];
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') consoleErrors.push(`${m.type()}: ${m.text()}`); });
    page.on('pageerror', e => consoleErrors.push('pageerror: ' + e.message));
    process.stderr.write(`[run-probes] ${url}\n`);
    const t0 = Date.now();
    await page.goto(url);
    try {
      await page.waitForFunction(() => document.title === 'DONE', null, { timeout: timeoutMs, polling: 250 });
      results[name] = await page.evaluate(() => window.__results);
    } catch (e) {
      results[name] = { ok: false, error: 'timeout/failure waiting for DONE: ' + e.message, partial: await page.evaluate(() => window.__results).catch(() => null) };
    }
    results[name].__automation = { wallMs: Date.now() - t0, consoleErrors };
    process.stderr.write(`[run-probes] ${name}: ${results[name].ok ? 'ok' : 'FAILED'} in ${Date.now() - t0} ms\n`);
    await page.close();
  }
} finally {
  await browser.close();
  server.kill();
}
const json = JSON.stringify(results, null, 2);
if (opts.out) writeFileSync(resolve(process.cwd(), opts.out), json);
console.log(json);
