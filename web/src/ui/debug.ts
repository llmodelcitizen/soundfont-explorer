/** Debug panel (D): switch latency p50/p95, decode path, fetch, cache, voices, protocol. Nothing leaves the browser. */
import type { Metrics } from '../audio/engine';
import { clear, fmtBytes, h, pct, percentile } from './dom';

export class DebugPanel {
  readonly el: HTMLElement;
  private body: HTMLElement;
  private last: Metrics | null = null;
  visible = false;

  constructor() {
    this.body = h('pre', { class: 'dbg-body' });
    const copy = h('button', { class: 'btn', type: 'button' }, 'copy JSON');
    copy.addEventListener('click', () => {
      if (this.last) void navigator.clipboard?.writeText(JSON.stringify(this.last, null, 1));
    });
    const close = h('button', { class: 'btn close', type: 'button', title: 'close (D)', 'aria-label': 'close debug panel' }, '×');
    close.addEventListener('click', () => this.toggle(false));
    this.el = h('aside', { class: 'debug hidden' }, h('div', { class: 'dbg-head' }, h('strong', null, 'debug'), h('div', { class: 'dbg-actions' }, copy, close)), this.body);
  }

  toggle(force?: boolean): void {
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
  }

  update(m: Metrics, protocol: string, diag: Record<string, string | number> = {}): void {
    this.last = { ...m, ...(diag as object) } as Metrics;
    if (!this.visible) return;
    const lat = m.switchLatencyMs;
    const lines = [
      `switch latency  n=${lat.length}  p50=${percentile(lat, 50).toFixed(1)} ms  p95=${percentile(lat, 95).toFixed(1)} ms  (+ output ${Math.round(m.outputLatency * 1000)} ms)`,
      `audio           sr=${m.sampleRate}  baseLatency=${(m.baseLatency * 1000).toFixed(1)} ms  voices=${m.voices}  stolen=${m.stolen}  late starts=${m.lateStarts}`,
      `decode          ${m.decodeKind}  avg ${m.decodeAvgMs.toFixed(1)} ms/segment`,
      `fetch           avg ${m.fetchAvgMs.toFixed(0)} ms  ${fmtBytes(m.fetchBytes)}  errors=${m.fetchErrors}  inflight=${m.inflight}  queued=${m.queued}  ${protocol}`,
      `cache           decoded ${fmtBytes(m.decodedBytes)}  compressed ${fmtBytes(m.compressedBytes)}  hit ${pct(m.cacheHitRate)}`,
      `prefetch        R=${m.radius}  v=${m.velocity.toFixed(1)} rows/s`,
      ...Object.entries(diag).map(([k, v]) => `${k.padEnd(15)} ${String(v)}`),
    ];
    clear(this.body);
    this.body.textContent = lines.join('\n');
  }
}
