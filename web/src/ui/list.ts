/**
 * Variant list: plain DOM rows over a column model (ui/columns.ts) with a sticky, clickable
 * header for sorting. Two highlights: .sel = cursor, .audible = what you hear. A filtered-out-
 * but-playing variant stays visible in a sticky row at the top.
 */
import type { CatalogDoc } from '../contracts/catalog';
import type { SetDoc } from '../contracts/set';
import { cellText, chipLabel, columnDef, columnTitle, displayLabel, rowMinWidth, visibleColumns, type CellContext, type ColKey, type ColumnDef } from './columns';
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

/** the sticky row shows #, chip and name at the same widths as the body rows */
const STICKY_COLS = [columnDef('idx').width, columnDef('chip').width, 'minmax(0, 1fr)'].join(' ');

export class VariantList {
  readonly el: HTMLElement;
  private rows: HTMLElement[] = [];
  private ids: string[] = [];
  private pos = new Map<string, number>();
  private sticky: HTMLElement;
  private head: HTMLElement;
  private body: HTMLElement;
  private selIndex = -1;
  private audibleId: string | null = null;
  private loadingId: string | null = null;
  private listened = new Set<string>();
  private favorites = new Set<string>();
  /** empty until setColumns(); the app passes the user's column preference right after construction */
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
    this.sticky.style.gridTemplateColumns = STICKY_COLS;
    this.body = h('div', { class: 'rows', role: 'listbox', 'aria-label': 'variants' });
    this.el = h('div', { class: 'list' }, this.head, this.sticky, this.body);
    this.body.addEventListener('click', (e) => {
      const row = (e.target as HTMLElement).closest('.row') as HTMLElement | null;
      if (row && row.dataset.index) this.cb.onClick(Number(row.dataset.index));
    });
    this.sticky.addEventListener('click', () => {
      if (this.audibleId) this.cb.onStickyClick(this.audibleId);
    });
  }

  /** choose visible columns (always-on ones are forced) and rebuild */
  setColumns(keys: ColKey[]): void {
    this.cols = visibleColumns(keys);
    this.el.style.setProperty('--cols', this.cols.map((c) => c.width).join(' '));
    // every column keeps its width; when they do not fit, the list scrolls sideways instead of
    // squeezing the name away (computed from the column definitions: setColumns() runs before
    // the list is in the document, where getComputedStyle() has no font size to measure)
    this.el.style.setProperty('--row-min', rowMinWidth(this.cols));
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
        { type: 'button', class: `cell col-${c.key} hcell${c.align ? ' ' + c.align : ''}${active ? ' sorted' : ''}`, title: `${columnTitle(c, this.set)} — click to sort`, 'aria-sort': active ? (this.sort.dir === 1 ? 'ascending' : 'descending') : 'none' },
        h('span', { class: 'hlabel' }, c.label),
        active && !c.noArrow ? h('span', { class: 'arrow' }, this.sort.dir === 1 ? '▲' : '▼') : '',
      );
      cell.addEventListener('click', () => this.cb.onSort(c.key));
      this.head.appendChild(cell);
    }
  }

  setItems(ids: string[]): void {
    this.ids = ids;
    this.pos = new Map(ids.map((id, i) => [id, i]));
    clear(this.body);
    this.rows = ids.map((id, i) => this.makeRow(id, i));
    for (const r of this.rows) this.body.appendChild(r);
    this.applyHighlights();
  }

  private makeRow(id: string, i: number): HTMLElement {
    const v = this.catalog.byId.get(id);
    const row = h('div', { class: 'row', role: 'option', dataset: { index: String(i), id }, title: v?.label ?? id });
    for (const c of this.cols) {
      const cell = h('span', { class: `cell col-${c.key}${c.align ? ' ' + c.align : ''} ${c.cls}` }, c.glyph ?? cellText(c.key, id, i, this.ctx));
      if (c.key === 'chip') cell.classList.add(`e-${v?.engine ?? 'x'}`);
      row.appendChild(cell);
    }
    return row;
  }

  private rowOf(id: string | null): HTMLElement | undefined {
    const i = id === null ? undefined : this.pos.get(id);
    return i === undefined ? undefined : this.rows[i];
  }

  /** move a single-row class from the row that had it to `next` (either may be missing) */
  private static move(cls: string, prev: HTMLElement | undefined, next: HTMLElement | undefined): void {
    prev?.classList.remove(cls);
    next?.classList.add(cls);
  }

  select(index: number, scroll = true): void {
    const previousIndex = this.selIndex;
    const prev = this.rows[this.selIndex];
    this.selIndex = index;
    const next = this.rows[index];
    VariantList.move('sel', prev, next);
    prev?.removeAttribute('aria-selected');
    next?.setAttribute('aria-selected', 'true');
    if (scroll) {
      // Reveal the selection first (including its sticky-header margin), then one row in the
      // direction of travel so keyboard/touch scrolling previews what will play next.
      next?.scrollIntoView({ block: 'nearest' });
      this.rows[index + (index < previousIndex ? -1 : 1)]?.scrollIntoView({ block: 'nearest' });
    }
  }

  setAudible(id: string | null): void {
    const prev = this.rowOf(this.audibleId);
    this.audibleId = id;
    VariantList.move('audible', prev, this.rowOf(id));
    this.updateSticky();
  }

  setLoading(id: string | null): void {
    const prev = this.rowOf(this.loadingId);
    this.loadingId = id;
    VariantList.move('loading', prev, this.rowOf(id));
  }

  /** variants whose dot is lit (listened ≥ threshold) */
  setListened(ids: Set<string>): void {
    this.listened = ids;
    this.rows.forEach((r, i) => r.classList.toggle('cached', this.listened.has(this.ids[i]!)));
  }

  setFavorites(ids: Set<string>): void {
    this.favorites = ids;
    this.rows.forEach((r, i) => r.classList.toggle('favorite', this.favorites.has(this.ids[i]!)));
  }

  markListened(id: string): void {
    if (this.listened.has(id)) return;
    this.listened.add(id);
    this.rowOf(id)?.classList.add('cached');
  }

  /** full pass over fresh rows (setItems); the single-row marks are moved incrementally afterwards */
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
    this.updateSticky();
  }

  /** the audible variant is hidden by the filters: show it in the sticky row */
  private updateSticky(): void {
    if (this.audibleId && !this.pos.has(this.audibleId)) {
      const v = this.catalog.byId.get(this.audibleId);
      clear(this.sticky);
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
