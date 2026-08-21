/**
 * Variant list: plain DOM rows (index · engine/chip badge · label · meta · status dot).
 * Two highlights: .sel = cursor, .audible = what you hear. A filtered-out-but-playing variant
 * stays visible in a sticky row at the top.
 */
import type { CatalogDoc, Variant } from '../contracts/catalog';
import type { SetDoc } from '../contracts/set';
import { clear, fmtBytes, h } from './dom';

export interface ListCallbacks {
  onClick(index: number): void;
  onStickyClick(variantId: string): void;
}

export function engineBadge(v: Variant | undefined): string {
  if (!v) return '?';
  const chip = (v.chip || '').toUpperCase();
  switch (v.engine) {
    case 'fluidsynth':
      return 'SF2';
    case 'adlmidi':
      return chip || 'OPL3';
    case 'opnmidi':
      return chip || 'OPN2';
    case 'edmidi':
      return chip || 'OPLL';
    case 'timidity':
      return 'GUS';
    case 'sc55':
      return 'SC-55';
    case 'munt':
      return chip === 'LA' ? 'MT-32' : chip || 'LA';
    default:
      return chip || v.engine.toUpperCase();
  }
}

export function metaLine(v: Variant | undefined): string {
  if (!v) return '';
  const parts: string[] = [];
  if (v.source?.bytes) parts.push(fmtBytes(v.source.bytes));
  const f = v.facets ?? {};
  if (f.completeness && f.completeness !== 'full_gm') parts.push(String(f.completeness).replace('_', ' '));
  if (f.lineage && f.lineage !== 'generic' && f.lineage !== 'fm_bank') parts.push(String(f.lineage));
  if (f.decade && f.decade !== 'unknown') parts.push(String(f.decade));
  if (v.bank && typeof v.bank['family'] === 'string') parts.push(String(v.bank['family']));
  return parts.join(' · ');
}

export class VariantList {
  readonly el: HTMLElement;
  private rows: HTMLElement[] = [];
  private ids: string[] = [];
  private sticky: HTMLElement;
  private body: HTMLElement;
  private selIndex = -1;
  private audibleId: string | null = null;
  private loadingId: string | null = null;
  private doneIds = new Set<string>();

  constructor(private catalog: CatalogDoc, private set: SetDoc, private cb: ListCallbacks) {
    this.sticky = h('div', { class: 'row sticky hidden', role: 'option' });
    this.body = h('div', { class: 'rows', role: 'listbox', 'aria-label': 'variants' });
    this.el = h('div', { class: 'list' }, this.sticky, this.body);
    this.body.addEventListener('click', (e) => {
      const row = (e.target as HTMLElement).closest('.row') as HTMLElement | null;
      if (row && row.dataset.index) this.cb.onClick(Number(row.dataset.index));
    });
    this.sticky.addEventListener('click', () => {
      if (this.audibleId) this.cb.onStickyClick(this.audibleId);
    });
  }

  setItems(ids: string[]): void {
    this.ids = ids;
    clear(this.body);
    this.rows = ids.map((id, i) => this.makeRow(id, i));
    for (const r of this.rows) this.body.appendChild(r);
    this.applyHighlights();
  }

  get length(): number {
    return this.ids.length;
  }

  indexOf(id: string): number {
    return this.ids.indexOf(id);
  }

  idAt(i: number): string | undefined {
    return this.ids[i];
  }

  private makeRow(id: string, i: number): HTMLElement {
    const v = this.catalog.byId.get(id);
    const sv = this.set.variants[id];
    const row = h(
      'div',
      { class: 'row', role: 'option', dataset: { index: String(i), id }, title: v?.label ?? id },
      h('span', { class: 'idx' }, String(i + 1)),
      h('span', { class: `badge e-${v?.engine ?? 'x'}` }, engineBadge(v)),
      h('span', { class: 'label' }, v?.label ?? id),
      h('span', { class: 'meta' }, metaLine(v)),
      h('span', { class: 'gain', title: 'applied gain' }, sv ? `${sv.gain_db >= 0 ? '+' : ''}${sv.gain_db.toFixed(1)} dB` : ''),
      h('span', { class: 'dot', 'aria-hidden': 'true' }),
    );
    return row;
  }

  select(index: number, scroll = true): void {
    this.selIndex = index;
    this.applyHighlights();
    if (scroll) this.rows[index]?.scrollIntoView({ block: 'nearest' });
  }

  setAudible(id: string | null): void {
    this.audibleId = id;
    if (id) this.doneIds.add(id);
    this.applyHighlights();
  }

  setLoading(id: string | null): void {
    this.loadingId = id;
    this.applyHighlights();
  }

  private applyHighlights(): void {
    this.rows.forEach((r, i) => {
      const id = this.ids[i]!;
      r.classList.toggle('sel', i === this.selIndex);
      r.classList.toggle('audible', id === this.audibleId);
      r.classList.toggle('loading', id === this.loadingId);
      r.classList.toggle('cached', this.doneIds.has(id));
      if (i === this.selIndex) r.setAttribute('aria-selected', 'true');
      else r.removeAttribute('aria-selected');
    });
    // sticky row when the audible variant is filtered out
    const visible = this.audibleId !== null && this.ids.includes(this.audibleId);
    if (this.audibleId && !visible) {
      const v = this.catalog.byId.get(this.audibleId);
      clear(this.sticky);
      this.sticky.append(
        h('span', { class: 'idx' }, '▶'),
        h('span', { class: `badge e-${v?.engine ?? 'x'}` }, engineBadge(v)),
        h('span', { class: 'label' }, v?.label ?? this.audibleId),
        h('span', { class: 'meta' }, 'playing (hidden by filters)'),
      );
      this.sticky.classList.remove('hidden');
      this.sticky.classList.add('audible');
    } else {
      this.sticky.classList.add('hidden');
    }
  }
}
