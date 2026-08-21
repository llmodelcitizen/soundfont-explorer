/**
 * Variant list: plain DOM rows over a column model (ui/columns.ts) with a sticky, clickable
 * header for sorting. Two highlights: .sel = cursor, .audible = what you hear. A filtered-out-
 * but-playing variant stays visible in a sticky row at the top.
 */
import type { CatalogDoc, Variant } from '../contracts/catalog';
import type { SetDoc } from '../contracts/set';
import { COLUMNS, cellText, chipLabel, displayLabel, type CellContext, type ColKey, type ColumnDef } from './columns';
import { clear, h } from './dom';

export interface ListCallbacks {
  onClick(index: number): void;
  onStickyClick(variantId: string): void;
  onSort(key: ColKey): void;
}

export interface SortState {
  key: ColKey | null;
  dir: 1 | -1;
}

export { chipLabel as engineBadge };

export function metaLine(v: Variant | undefined): string {
  if (!v) return '';
  const parts: string[] = [];
  const f = v.facets ?? {};
  if (f.completeness && f.completeness !== 'full_gm') parts.push(String(f.completeness).replace('_', ' '));
  if (f.lineage && f.lineage !== 'generic' && f.lineage !== 'fm_bank') parts.push(String(f.lineage));
  return parts.join(' · ');
}

export class VariantList {
  readonly el: HTMLElement;
  private rows: HTMLElement[] = [];
  private ids: string[] = [];
  private sticky: HTMLElement;
  private head: HTMLElement;
  private body: HTMLElement;
  private selIndex = -1;
  private audibleId: string | null = null;
  private loadingId: string | null = null;
  private listened = new Set<string>();
  private favorites = new Set<string>();
  private cols: ColumnDef[] = [];
  private sort: SortState = { key: null, dir: 1 };
  private ctx: CellContext;

  constructor(
    private catalog: CatalogDoc,
    private set: SetDoc,
    private cb: ListCallbacks,
    hooks: { isFavorite(id: string): boolean; listenedSeconds(id: string): number; listened(id: string): boolean },
  ) {
    this.ctx = { catalog, set, ...hooks };
    this.head = h('div', { class: 'row head', role: 'row' });
    this.sticky = h('div', { class: 'row sticky hidden', role: 'option' });
    this.body = h('div', { class: 'rows', role: 'listbox', 'aria-label': 'variants' });
    this.el = h('div', { class: 'list' }, this.head, this.sticky, this.body);
    this.body.addEventListener('click', (e) => {
      const row = (e.target as HTMLElement).closest('.row') as HTMLElement | null;
      if (row && row.dataset.index) this.cb.onClick(Number(row.dataset.index));
    });
    this.sticky.addEventListener('click', () => {
      if (this.audibleId) this.cb.onStickyClick(this.audibleId);
    });
    this.setColumns(COLUMNS.filter((c) => c.defaultOn).map((c) => c.key));
  }

  /** choose visible columns (always-on ones are forced) and rebuild */
  setColumns(keys: ColKey[]): void {
    const want = new Set(keys);
    this.cols = COLUMNS.filter((c) => c.always || want.has(c.key));
    this.el.style.setProperty('--cols', this.cols.map((c) => c.width).join(' '));
    // every column keeps its width; when they do not fit, the list scrolls sideways instead of squeezing the name away
    const em = parseFloat(getComputedStyle(this.el).fontSize) || 14;
    const minPx = this.cols.reduce((n, c) => n + (/^minmax\(([\d.]+)em/.exec(c.width)?.[1] ? parseFloat(/^minmax\(([\d.]+)em/.exec(c.width)![1]!) * em : parseFloat(c.width) * em), 0) + 8 * (this.cols.length - 1) + 26;
    this.el.style.setProperty('--row-min', `${Math.round(minPx)}px`);
    this.renderHead();
    this.setItems(this.ids);
  }

  get columns(): ColKey[] {
    return this.cols.map((c) => c.key);
  }

  setSort(sort: SortState): void {
    this.sort = sort;
    this.renderHead();
  }

  private renderHead(): void {
    clear(this.head);
    for (const c of this.cols) {
      const active = this.sort.key === c.key;
      const cell = h(
        'button',
        { type: 'button', class: `cell col-${c.key} hcell${c.align ? ' ' + c.align : ''}${active ? ' sorted' : ''}`, title: `${c.title} — click to sort`, 'aria-sort': active ? (this.sort.dir === 1 ? 'ascending' : 'descending') : 'none' },
        c.label,
        active && !c.noArrow ? h('span', { class: 'arrow' }, this.sort.dir === 1 ? '▲' : '▼') : '',
      );
      cell.addEventListener('click', () => this.cb.onSort(c.key));
      this.head.appendChild(cell);
    }
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
    const row = h('div', { class: 'row', role: 'option', dataset: { index: String(i), id }, title: v?.label ?? id });
    for (const c of this.cols) {
      const txt = cellText(c.key, id, i, this.ctx);
      const cell = h('span', { class: `cell col-${c.key}${c.align ? ' ' + c.align : ''}` }, c.key === 'fav' ? '♥' : c.key === 'dot' ? '' : txt);
      if (c.key === 'chip') cell.classList.add('badge', `e-${v?.engine ?? 'x'}`);
      if (c.key === 'label') cell.classList.add('label');
      if (c.key === 'fav') cell.classList.add('fav');
      if (c.key === 'dot') cell.classList.add('dot');
      if (c.key === 'idx') cell.classList.add('idx');
      if (c.key === 'gain' || c.key === 'lufs') cell.classList.add('num');
      if (c.key !== 'label' && c.key !== 'chip' && c.key !== 'idx' && c.key !== 'fav' && c.key !== 'dot') cell.classList.add('meta');
      row.appendChild(cell);
    }
    return row;
  }

  select(index: number, scroll = true): void {
    this.selIndex = index;
    this.applyHighlights();
    if (scroll) this.rows[index]?.scrollIntoView({ block: 'nearest' });
  }

  setAudible(id: string | null): void {
    this.audibleId = id;
    this.applyHighlights();
  }

  setLoading(id: string | null): void {
    this.loadingId = id;
    this.applyHighlights();
  }

  /** variants whose dot is lit (listened ≥ threshold) */
  setListened(ids: Set<string>): void {
    this.listened = ids;
    this.applyHighlights();
  }

  setFavorites(ids: Set<string>): void {
    this.favorites = ids;
    this.rows.forEach((r, i) => r.classList.toggle('favorite', this.favorites.has(this.ids[i]!)));
  }

  markListened(id: string): void {
    if (this.listened.has(id)) return;
    this.listened.add(id);
    const i = this.ids.indexOf(id);
    if (i >= 0) this.rows[i]?.classList.add('cached');
  }

  private applyHighlights(): void {
    this.rows.forEach((r, i) => {
      const id = this.ids[i]!;
      r.classList.toggle('sel', i === this.selIndex);
      r.classList.toggle('audible', id === this.audibleId);
      r.classList.toggle('loading', id === this.loadingId);
      r.classList.toggle('cached', this.listened.has(id));
      r.classList.toggle('favorite', this.favorites.has(id));
      if (i === this.selIndex) r.setAttribute('aria-selected', 'true');
      else r.removeAttribute('aria-selected');
    });
    const visible = this.audibleId !== null && this.ids.includes(this.audibleId);
    if (this.audibleId && !visible) {
      const v = this.catalog.byId.get(this.audibleId);
      clear(this.sticky);
      this.sticky.style.gridTemplateColumns = '3.2em 4.6em minmax(0, 1fr)';
      this.sticky.append(
        h('span', { class: 'cell idx' }, '▶'),
        h('span', { class: `cell badge e-${v?.engine ?? 'x'}` }, chipLabel(v)),
        h('span', { class: 'cell label' }, displayLabel(v, this.audibleId), h('span', { class: 'meta' }, ' — playing (hidden by filters)')),
      );
      this.sticky.classList.remove('hidden');
      this.sticky.classList.add('audible');
    } else {
      this.sticky.classList.add('hidden');
    }
  }
}
