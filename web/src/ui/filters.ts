/** Filter bar: facet groups with live counts, OR within / AND across; search box; hidden chip. */
import { FACET_KEYS, FACET_LABELS, type FacetKey, type FilterIndex, type Selection } from '../state/filterIndex';
import { clear, h, setPressed } from './dom';
import { FACET_HELP, HelpTips } from './facetHelp';

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

/** anchor a help bubble under its trigger, nudged to stay inside the viewport (it is position: fixed) */
function placeTip(btn: HTMLElement, tip: HTMLElement): void {
  tip.style.left = '0px';
  tip.style.top = '0px';
  const anchor = btn.getBoundingClientRect();
  const box = tip.getBoundingClientRect();
  if (!box.width || !box.height) return; // never laid out: nothing sensible to anchor to
  const pad = 8;
  const gap = 6;
  const below = anchor.bottom + gap;
  const above = anchor.top - gap - box.height;
  tip.style.left = `${Math.round(Math.min(Math.max(pad, anchor.left), Math.max(pad, window.innerWidth - pad - box.width)))}px`;
  tip.style.top = `${Math.round(below + box.height > window.innerHeight - pad && above >= pad ? above : below)}px`;
}

export class FilterBar {
  readonly el: HTMLElement;
  readonly search: HTMLInputElement;
  private groups: HTMLElement;
  private hiddenChip: HTMLElement;
  sel: Selection = {};
  query = '';
  favoritesOnly = false;
  private favBtn!: HTMLButtonElement;
  /** the per-category "?" trigger and its bubble, rebuilt with the panel */
  private helpEls = new Map<FacetKey, { btn: HTMLElement; tip: HTMLElement }>();
  private help = new HelpTips((open) => this.paintHelp(open));
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
    // the bubble is fixed, so the panel's own scrolling has to be passed on to it
    this.groups.addEventListener('scroll', () => this.trackHelp());
    this.el = h('div', { class: 'filterbar' }, h('div', { class: 'filterrow' }, toggle, this.favBtn, this.hiddenChip, this.search), this.groups);
  }

  toggle(force?: boolean): void {
    const next = force ?? !this.open;
    if (next === this.open) return;
    this.open = next;
    if (next && this.stale) this.render();
    else if (!next) this.help.close(); // a bubble must not come back with the panel
    this.groups.classList.toggle('hidden', !next);
    this.cb.onOpenChange?.(next);
  }

  clearAll(): void {
    this.sel = {};
    this.query = '';
    this.search.value = '';
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

  /** the "?" beside a category heading: hover, keyboard focus or tap shows the same description */
  private helpFor(key: FacetKey): HTMLElement {
    const id = `facet-help-${key}`;
    const btn = h('button', { type: 'button', class: 'facet-help-btn', 'aria-expanded': 'false', 'aria-describedby': id, 'aria-label': `about the ${FACET_LABELS[key]} filter` }, '?');
    const tip = h('div', { class: 'facet-tip hidden', role: 'tooltip', id }, FACET_HELP[key]);
    const wrap = h('span', { class: 'facet-help' }, btn, tip);
    // on the wrapper, so reading the bubble itself keeps it open
    wrap.addEventListener('mouseenter', () => this.help.pointerEnter(key));
    wrap.addEventListener('mouseleave', () => this.help.pointerLeave(key));
    btn.addEventListener('focus', () => this.help.focus(key));
    btn.addEventListener('blur', () => this.help.blur(key));
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.help.activate(key);
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        // an Escape that closed a bubble stops here; one with nothing open still closes the panel
        if (this.help.escape()) e.stopPropagation();
      } else if (e.key === ' ' || e.key === 'Enter') {
        // the button's own activation is enough: the global keymap must not also play/pause
        e.stopPropagation();
      }
    });
    this.helpEls.set(key, { btn, tip });
    return wrap;
  }

  /** one bubble visible at a time, and every trigger's state announced */
  private paintHelp(open: FacetKey | null): void {
    for (const [key, { btn, tip }] of this.helpEls) {
      const on = key === open;
      btn.setAttribute('aria-expanded', on ? 'true' : 'false');
      tip.classList.toggle('hidden', !on);
      if (on) placeTip(btn, tip);
    }
  }

  /** keep the open bubble on its trigger while the panel scrolls; drop it once the trigger is gone */
  private trackHelp(): void {
    const key = this.help.open;
    const els = key && this.helpEls.get(key);
    if (!els) return;
    const btn = els.btn.getBoundingClientRect();
    const panel = this.groups.getBoundingClientRect();
    if (btn.bottom <= panel.top || btn.top >= panel.bottom) this.help.close();
    else placeTip(els.btn, els.tip);
  }

  private render(): void {
    this.stale = false;
    this.help.reset();
    this.helpEls.clear();
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
      this.groups.appendChild(h('div', { class: 'facet' }, h('div', { class: 'facet-name' }, FACET_LABELS[key], this.helpFor(key)), h('div', { class: 'opts' }, opts)));
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
