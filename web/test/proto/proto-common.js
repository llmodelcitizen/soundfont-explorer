// Shared helpers for the M0b prototype pages. Plain ES module, no build step.
// Served by serve.py from the repo root, so absolute paths below start at the repo root.

export const REAL_ROOT = '/work/derisk/e1m1';      // produced by the M0b-1 render agent
export const SYNTH_ROOT = '/work/proto/synth';     // synthetic stand-in (A, B)
export const LEAD_IN_S = 0.12, LEAD_OUT_S = 0.02, SLICE_S = 2.0, SEG_SAMPLES = 102720;

export const params = new URLSearchParams(location.search);

export async function ls(dir) {
  const r = await fetch('/__ls/' + dir.replace(/^\//, ''));
  if (!r.ok) return null;
  return r.json();
}

// Returns { kind: 'real'|'synthetic', root, variants: [{id, dir, segs:[names...]}] }
// Variants must have a seg/ dir with 2.14 s Opus slices; takes the first `count` that qualify.
export async function discoverVariants(count = 2, minSegs = 6) {
  for (const [kind, root] of [['real', REAL_ROOT], ['synthetic', SYNTH_ROOT]]) {
    if (params.get('audio') === 'synthetic' && kind === 'real') continue;
    const top = await ls(root);
    if (!top) continue;
    const out = [];
    for (const d of top.dirs) {
      const seg = await ls(`${root}/${d}/seg`);
      if (!seg || seg.files.length < minSegs) continue;
      const segs = seg.files.filter(f => f.endsWith('.opus')).sort();
      if (segs.length < minSegs) continue;
      out.push({ id: d, dir: `${root}/${d}`, segs });
      if (out.length >= count) break;
    }
    if (out.length >= Math.min(count, 1)) return { kind, root, variants: out };
  }
  throw new Error('no audio found: neither ' + REAL_ROOT + ' nor ' + SYNTH_ROOT + ' has variants with seg/*.opus');
}

export async function fetchBytes(url, init) {
  const t0 = performance.now();
  const r = await fetch(url, init);
  if (!r.ok && r.status !== 206) throw new Error(`${url}: HTTP ${r.status}`);
  const buf = await r.arrayBuffer();
  return { buf, ms: performance.now() - t0, status: r.status, bytes: buf.byteLength };
}

export function rmsOf(audioBuffer) {
  let sum = 0, n = 0;
  for (let c = 0; c < audioBuffer.numberOfChannels; c++) {
    const d = audioBuffer.getChannelData(c);
    for (let i = 0; i < d.length; i++) sum += d[i] * d[i];
    n += d.length;
  }
  return n ? Math.sqrt(sum / n) : 0;
}
export function rmsOfChannels(channelData) {
  let sum = 0, n = 0;
  for (const d of channelData) { for (let i = 0; i < d.length; i++) sum += d[i] * d[i]; n += d.length; }
  return n ? Math.sqrt(sum / n) : 0;
}
export function peakOf(audioBuffer) {
  let p = 0;
  for (let c = 0; c < audioBuffer.numberOfChannels; c++) {
    const d = audioBuffer.getChannelData(c);
    for (let i = 0; i < d.length; i++) { const a = Math.abs(d[i]); if (a > p) p = a; }
  }
  return p;
}
export const dB = x => x > 0 ? 20 * Math.log10(x) : -Infinity;

export function percentile(arr, p) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const idx = Math.min(s.length - 1, Math.max(0, Math.ceil(p / 100 * s.length) - 1));
  return s[idx];
}
export function stats(arr) {
  if (!arr.length) return { n: 0 };
  const s = [...arr].sort((a, b) => a - b);
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  return { n: s.length, min: s[0], p50: percentile(s, 50), p95: percentile(s, 95), max: s[s.length - 1], mean };
}
export const r3 = x => (x == null || !isFinite(x)) ? x : Math.round(x * 1000) / 1000;

export function envInfo(ctx) {
  return {
    userAgent: navigator.userAgent,
    hardwareConcurrency: navigator.hardwareConcurrency,
    platform: navigator.platform,
    touch: navigator.maxTouchPoints > 0,
    ctxSampleRate: ctx ? ctx.sampleRate : null,
    baseLatency: ctx ? ctx.baseLatency : null,
    outputLatency: ctx ? ctx.outputLatency : null,
    audioWorklet: !!(ctx && ctx.audioWorklet),
    wasm: typeof WebAssembly === 'object',
    time: new Date().toISOString(),
  };
}

// --- page output helpers -------------------------------------------------------------------
export function logLine(msg, cls) {
  const el = document.getElementById('log');
  const div = document.createElement('div');
  if (cls) div.className = cls;
  div.textContent = msg;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
  return div;
}
export function showResults(obj) {
  const pre = document.getElementById('results');
  pre.textContent = JSON.stringify(obj, null, 2);
}
export function finish(results) {
  window.__results = results;
  showResults(results);
  document.title = 'DONE';
}
export function fail(err) {
  const results = { ok: false, error: String(err && err.stack || err) };
  logLine('ERROR ' + results.error, 'bad');
  finish(results);
}

// Button gate: resolves on click (or immediately with ?autostart=1 for automation).
export function gate(buttonId, label) {
  const b = document.getElementById(buttonId);
  if (label) b.textContent = label;
  return new Promise(res => {
    if (params.get('autostart') === '1') { b.disabled = true; res('auto'); return; }
    b.addEventListener('click', () => { b.disabled = true; res('click'); }, { once: true });
  });
}

export const sleep = ms => new Promise(r => setTimeout(r, ms));
