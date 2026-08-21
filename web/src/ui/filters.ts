/** Filter bar: facet groups with live counts, OR within / AND across; search box; hidden chip. */
import { FACET_KEYS, FACET_LABELS, type FacetKey, type FilterIndex, type Selection } from '../state/filterIndex';
import { clear, h, setPressed } from './dom';

export interface FilterCallbacks {
  onChange(sel: Selection, query: string): void;
  onSearchEnter(): void;
  onFavoritesOnly(on: boolean): void;
  /** the facet panel opened/closed (the app hides Now Playing and arms a tap-to-close scrim on mobile) */
  onOpenChange?(open: boolean): void;
}

export const VALUE_LABELS: Record<string, string> = {
  full_gm: 'full GM',
  melodic_only: 'melodic only',
  drums_only: 'drums only',
  single_instrument: 'single instrument',
  fm_bank: 'FM bank',
  fm_sampled: 'FM (sampled)',
  yamaha_xg: 'Yamaha XG',
  gs_compat: 'GS-compatible',
  pcm_rom: 'PCM ROM',
  opn2: 'OPN2 (YM2612)',
  opna: 'OPNA (YM2608)',
  opll: 'OPLL (YM2413)',
  scc: 'SCC',
  opl2: 'OPL2',
  opl3: 'OPL3',
  esfm: 'ESFM',
  cqm: 'CQM',
  la: 'LA (MT-32)',
  gus: 'GUS patches',
  sf2: 'SoundFont',
  fm: 'FM',
  sampled: 'sampled',
  gravis: 'Gravis',
  fluidsynth: 'FluidSynth (SF2)',
  adlmidi: 'libADLMIDI',
  opnmidi: 'libOPNMIDI',
  edmidi: 'libEDMIDI',
  timidity: 'TiMidity++ (GUS)',
  sc55: 'Nuked-SC55',
  munt: 'Munt (MT-32)',
};

export const label = (v: string): string => VALUE_LABELS[v] ?? v.replace(/_/g, ' ');

export class FilterBar {
  readonly el: HTMLElement;
  readonly search: HTMLInputElement;
  private groups: HTMLElement;
  private hiddenChip: HTMLElement;
  sel: Selection = {};
  query = '';
  favoritesOnly = false;
  private favBtn!: HTMLButtonElement;
  private open = false;
  /** the facet panel is rebuilt lazily: only while open, or on opening if filters changed meanwhile */
  private stale = true;

  constructor(private readonly index: FilterIndex, initial: Selection, initialQuery: string, private cb: FilterCallbacks) {
    this.sel = initial;
    this.query = initialQuery;
    this.search = h('input', { type: 'search', class: 'search', placeholder: 'search  ( / )', value: initialQuery, 'aria-label': 'search variants' });
    this.search.addEventListener('input', () => {
      this.query = this.search.value;
      this.emit();
    });
    this.search.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        this.cb.onSearchEnter();
      } else if (e.key === 'Escape') {
        this.search.value = '';
        this.query = '';
        this.emit();
        this.search.blur();
      }
      e.stopPropagation();
    });
    this.hiddenChip = h('button', { class: 'chip hidden', type: 'button', title: 'clear all filters' });
    this.hiddenChip.addEventListener('click', () => this.clearAll());
    const toggle = h('button', { class: 'btn', type: 'button', title: 'filters (F)' }, 'filters ▾');
    toggle.addEventListener('click', () => this.toggle());
    this.favBtn = h('button', { class: 'btn toggle fav-filter', type: 'button', title: 'show favorites only', 'aria-pressed': 'false' }, '♥ favorites') as HTMLButtonElement;
    this.favBtn.addEventListener('click', () => {
      this.setFavoritesOnly(!this.favoritesOnly);
      this.cb.onFavoritesOnly(this.favoritesOnly);
      this.favBtn.blur();
    });
    this.groups = h('div', { class: 'facets hidden' });
    this.el = h('div', { class: 'filterbar' }, h('div', { class: 'filterrow' }, toggle, this.favBtn, this.hiddenChip, this.search), this.groups);
  }

  toggle(force?: boolean): void {
    const next = force ?? !this.open;
    if (next === this.open) return;
    this.open = next;
    if (next && this.stale) this.render();
    this.groups.classList.toggle('hidden', !next);
    this.cb.onOpenChange?.(next);
  }

  clearAll(): void {
    this.sel = {};
    this.setFavoritesOnly(false);
    this.cb.onFavoritesOnly(false);
    this.emit();
  }

  setFavoritesOnly(on: boolean): void {
    this.favoritesOnly = on;
    setPressed(this.favBtn, on);
  }

  private emit(): void {
    if (this.open) this.render();
    else this.stale = true;
    this.cb.onChange(this.sel, this.query);
  }

  /** number of rendered variants hidden by the current filters/search */
  updateHidden(total: number, visible: number): void {
    const hidden = total - visible;
    this.hiddenChip.textContent = hidden > 0 ? `+${hidden} hidden` : '';
    this.hiddenChip.classList.toggle('hidden', hidden <= 0);
  }

  private render(): void {
    this.stale = false;
    clear(this.groups);
    const all = this.index.allCounts(this.sel, this.query);
    for (const key of FACET_KEYS) {
      const counts = all[key];
      if (counts.length <= 1 && !(this.sel[key]?.size)) continue;
      const chosen = this.sel[key] ?? new Set<string>();
      const opts = counts.map(({ value, count }) => {
        const on = chosen.has(value);
        const b = h('button', { type: 'button', class: `opt${on ? ' on' : ''}${count === 0 && !on ? ' zero' : ''}`, dataset: { key, value } }, label(value), h('span', { class: 'cnt' }, String(count)));
        b.addEventListener('click', () => this.toggleValue(key, value));
        return b;
      });
      this.groups.appendChild(h('div', { class: 'facet' }, h('div', { class: 'facet-name' }, FACET_LABELS[key]), h('div', { class: 'opts' }, opts)));
    }
  }

  toggleValue(key: FacetKey, value: string): void {
    const s = new Set(this.sel[key] ?? []);
    if (s.has(value)) s.delete(value);
    else s.add(value);
    const next: Selection = { ...this.sel };
    if (s.size) next[key] = s;
    else delete next[key];
    this.sel = next;
    this.emit();
  }
}
