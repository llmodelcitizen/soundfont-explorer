/** Debug panel (D): live playback, audio, decode, fetch, cache, device and page diagnostics. Nothing leaves the browser. */
import type { Metrics } from '../audio/engine';
import { clear, fmtBytes, h, pct, percentile } from './dom';

export class DebugPanel {
  private static readonly POSITION_KEY = 'sfp.debug-position.v1';
  private static readonly MARGIN = 4;
  readonly el: HTMLElement;
  private body: HTMLElement;
  private last: Record<string, unknown> | null = null;
  private drag: { pointerId: number; dx: number; dy: number } | null = null;
  private savedPosition: { x: number; y: number } | null = null;
  visible = false;

  constructor() {
    this.body = h('pre', { class: 'dbg-body' });
    const copy = h('button', { class: 'btn', type: 'button', title: 'copy this diagnostic snapshot' }, 'copy JSON');
    copy.addEventListener('click', () => {
      if (this.last) void navigator.clipboard?.writeText(JSON.stringify(this.last, null, 1));
    });
    const close = h('button', { class: 'btn close', type: 'button', title: 'close (D)', 'aria-label': 'close debug panel' }, '×');
    close.addEventListener('click', () => this.toggle(false));
    const head = h('div', { class: 'dbg-head', title: 'drag to move · double-click to reset' }, h('strong', null, 'debug'), h('div', { class: 'dbg-actions' }, copy, close));
    this.el = h('aside', { class: 'debug hidden' }, head, this.body);
    try {
      const saved = JSON.parse(localStorage.getItem(DebugPanel.POSITION_KEY) ?? 'null') as { x?: unknown; y?: unknown } | null;
      if (saved && typeof saved.x === 'number' && typeof saved.y === 'number') this.savedPosition = { x: saved.x, y: saved.y };
    } catch { /* fresh/private browsing */ }
    head.addEventListener('pointerdown', (e) => this.startDrag(e));
    head.addEventListener('pointermove', (e) => this.moveDrag(e));
    head.addEventListener('pointerup', (e) => this.endDrag(e));
    head.addEventListener('pointercancel', (e) => this.endDrag(e));
    head.addEventListener('dblclick', (e) => {
      if ((e.target as Element).closest('button')) return;
      this.resetPosition();
    });
    window.addEventListener('resize', () => {
      if (this.visible && this.savedPosition) this.place(this.savedPosition.x, this.savedPosition.y);
    });
  }

  toggle(force?: boolean): void {
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (this.visible && this.savedPosition) requestAnimationFrame(() => this.place(this.savedPosition!.x, this.savedPosition!.y));
  }

  update(m: Metrics, protocol: string, diag: Record<string, string | number> = {}): void {
    this.last = { ...m, protocol, ...diag };
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

  private startDrag(e: PointerEvent): void {
    if (e.button !== 0 || (e.target as Element).closest('button')) return;
    e.preventDefault();
    const rect = this.el.getBoundingClientRect();
    this.drag = { pointerId: e.pointerId, dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    this.place(rect.left, rect.top);
    this.el.classList.add('dragging');
  }

  private moveDrag(e: PointerEvent): void {
    if (!this.drag || e.pointerId !== this.drag.pointerId) return;
    this.place(e.clientX - this.drag.dx, e.clientY - this.drag.dy);
  }

  private endDrag(e: PointerEvent): void {
    if (!this.drag || e.pointerId !== this.drag.pointerId) return;
    this.drag = null;
    this.el.classList.remove('dragging');
    try {
      localStorage.setItem(DebugPanel.POSITION_KEY, JSON.stringify(this.savedPosition));
    } catch { /* private browsing */ }
  }

  private place(x: number, y: number): void {
    const rect = this.el.getBoundingClientRect();
    const margin = DebugPanel.MARGIN;
    const left = Math.min(Math.max(margin, x), Math.max(margin, window.innerWidth - rect.width - margin));
    const top = Math.min(Math.max(margin, y), Math.max(margin, window.innerHeight - rect.height - margin));
    this.savedPosition = { x: left, y: top };
    this.el.style.left = `${left}px`;
    this.el.style.top = `${top}px`;
    this.el.style.right = 'auto';
    this.el.style.bottom = 'auto';
  }

  private resetPosition(): void {
    this.savedPosition = null;
    this.el.style.removeProperty('left');
    this.el.style.removeProperty('top');
    this.el.style.removeProperty('right');
    this.el.style.removeProperty('bottom');
    try {
      localStorage.removeItem(DebugPanel.POSITION_KEY);
    } catch { /* private browsing */ }
  }
}
