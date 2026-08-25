/** Filter bar: facet groups with live counts, OR within / AND across; search box; hidden chip. */
import { FACET_KEYS, FACET_LABELS, type FacetKey, type FilterIndex, type Selection } from '../state/filterIndex';
import { clear, h, setPressed } from './dom';
import { FACET_HELP, HelpTips, tipPosition } from './facetHelp';

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

/**
 * Grace after the pointer leaves a help trigger, before its bubble goes. The bubble sits against
 * its trigger, but a pointer heading for the middle of the text leaves the trigger sideways first
 * and crosses the heading — without the grace the bubble would vanish on the way to being read
 * (WCAG 1.4.13 Hoverable).
 */
const HOVER_GRACE_MS = 220;

/** a tap rather than a hover: a finger and a pen both emit enter/leave around their click */
const isTap = (e: PointerEvent): boolean => e.pointerType === 'touch' || e.pointerType === 'pen';

/** some of this element's copy is selected, so the click that ended the drag must not close it */
function selectionInside(el: HTMLElement): boolean {
  const sel = window.getSelection();
  return !!sel && !sel.isCollapsed && !!sel.anchorNode && el.contains(sel.anchorNode);
}

/** anchor a help bubble under its trigger, nudged to stay inside the viewport (it is position: fixed) */
function placeTip(btn: HTMLElement, tip: HTMLElement): void {
  tip.style.left = '0px';
  tip.style.top = '0px';
  const box = tip.getBoundingClientRect();
  if (!box.width || !box.height) return; // never laid out: nothing sensible to anchor to
  const at = tipPosition(btn.getBoundingClientRect(), box, { width: window.innerWidth, height: window.innerHeight });
  tip.style.left = `${at.left}px`;
  tip.style.top = `${at.top}px`;
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
  private helpEls = new Map<FacetKey, { btn: HTMLElement; tip: HTMLElement; wrap: HTMLElement }>();
  private help = new HelpTips((open) => this.paintHelp(open));
  private helpLeave: ReturnType<typeof setTimeout> | null = null;
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
    // the bubble is fixed, so the panel's own scrolling — and any viewport change under it, such
    // as a phone rotation — has to be passed on to it
    this.groups.addEventListener('scroll', () => this.trackHelp());
    window.addEventListener('resize', this.onResize);
    // A bubble is a transient overlay: a click or tap outside it drops it, and so does Escape.
    // Both listen in the capture phase, so the Escape that dismissed a bubble never reaches the
    // app keymap, which would close the whole filter panel with it (issue #26).
    document.addEventListener('pointerdown', this.onOutsidePointer, true);
    window.addEventListener('keydown', this.onEscape, true);
    this.el = h('div', { class: 'filterbar' }, h('div', { class: 'filterrow' }, toggle, this.favBtn, this.hiddenChip, this.search), this.groups);
  }

  /** the bar is rebuilt for every song: let go of the listeners that outlive its own DOM */
  dispose(): void {
    this.cancelHelpLeave();
    window.removeEventListener('resize', this.onResize);
    document.removeEventListener('pointerdown', this.onOutsidePointer, true);
    window.removeEventListener('keydown', this.onEscape, true);
  }

  private readonly onResize = (): void => this.trackHelp();

  private readonly onOutsidePointer = (e: Event): void => {
    if (e instanceof PointerEvent) this.outsideHelp(e);
  };

  /**
   * Escape drops an open bubble, and normally the key stops there: the app's own Escape closes the
   * whole filter panel, which is not what dismissing a bubble asked for. It does not stop there when
   * something nearer the user's attention wants the key — a text field it would clear, or a modal
   * covering the panel — because the bubble may only be open because the pointer happens to be
   * resting on a "?" (issue #26).
   */
  private readonly onEscape = (e: KeyboardEvent): void => {
    if (e.key !== 'Escape' || !this.help.open) return;
    const t = e.target as HTMLElement | null;
    const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    const modal = !!document.querySelector('.overlay:not(.hidden)');
    if (this.help.escape() && !typing && !modal) e.stopPropagation();
  };

  toggle(force?: boolean): void {
    const next = force ?? !this.open;
    if (next === this.open) return;
    this.open = next;
    if (next && this.stale) this.render();
    else if (!next) {
      // a bubble must not come back with the panel, nor the focus it was holding on to
      this.cancelHelpLeave();
      this.help.close();
    }
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
    const btn = h('button', { type: 'button', class: 'facet-help-btn', dataset: { open: 'false' }, 'aria-describedby': id, 'aria-label': `about the ${FACET_LABELS[key]} filter` }, '?');
    const tip = h('div', { class: 'facet-tip hidden', role: 'tooltip', id }, FACET_HELP[key]);
    const wrap = h('span', { class: 'facet-help' }, btn, tip);
    // On the wrapper, so reading the bubble itself keeps it open: it hangs against its trigger,
    // and a pointer that clips the heading on the way there has the grace above to arrive. A tap
    // emits the same enter/leave around its click: that is not hover, and taking it for hover left
    // the bubble already open when the tap's click arrived, which toggled it straight back shut.
    // A pen reports 'pen' and taps exactly like a finger, so it is held to the same path (#26).
    wrap.addEventListener('pointerenter', (e) => {
      if (isTap(e)) return;
      this.cancelHelpLeave();
      this.help.pointerEnter(key);
    });
    wrap.addEventListener('pointerleave', (e) => {
      if (isTap(e)) return;
      this.cancelHelpLeave();
      this.helpLeave = setTimeout(() => {
        this.helpLeave = null;
        this.help.pointerLeave(key);
      }, HOVER_GRACE_MS);
    });
    // A click or tap on the bubble is aimed at the filter chips it covers, so it takes the bubble
    // down — on the click rather than the press, so that the gesture is spent here instead of
    // toggling a chip the reader could not see. A mouse drag that selected some of the copy is the
    // exception: that click ends a selection and must not throw the text away with it.
    tip.addEventListener('click', () => {
      if (!selectionInside(tip)) this.help.close();
    });
    // pointerdown, not click: the press is what decides whether the focus that follows it is the
    // keyboard's (which holds the bubble open) or a pointer's (which does not)
    btn.addEventListener('pointerdown', () => this.help.pointerDown());
    btn.addEventListener('focus', () => this.help.focus(key));
    btn.addEventListener('blur', () => this.help.blur(key));
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.help.activate(key);
    });
    btn.addEventListener('keydown', (e) => {
      // the button's own activation is enough: the global keymap must not also play/pause. Tab is
      // deliberately left alone — keyboard.ts lets it walk the panel so every trigger is reachable.
      if (e.key === ' ' || e.key === 'Enter') e.stopPropagation();
    });
    this.helpEls.set(key, { btn, tip, wrap });
    return wrap;
  }

  private cancelHelpLeave(): void {
    if (this.helpLeave === null) return;
    clearTimeout(this.helpLeave);
    this.helpLeave = null;
  }

  /**
   * A click or tap anywhere but the open bubble and its own trigger drops it. The gesture is not
   * swallowed: a bubble is a tooltip, not a modal, so a tap that lands on a chip the reader can see
   * both dismisses the bubble and presses the chip. Only a tap on the bubble itself is spent, since
   * there the chip underneath is hidden by the copy (issue #26).
   */
  private outsideHelp(e: PointerEvent): void {
    const key = this.help.open;
    const els = key && this.helpEls.get(key);
    if (!els) return;
    const target = e.target;
    if (target instanceof Node && els.wrap.contains(target)) return;
    this.help.close();
  }

  /** one bubble visible at a time, and every trigger's state announced */
  private paintHelp(open: FacetKey | null): void {
    for (const [key, { btn, tip }] of this.helpEls) {
      const on = key === open;
      // a tooltip is not a disclosure: the stylesheets key off data-open, and a screen reader
      // gets the copy from aria-describedby whether the bubble is painted or not
      btn.dataset.open = on ? 'true' : 'false';
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
    this.cancelHelpLeave();
    this.help.close();
    this.helpEls.clear();
    // every chip is rebuilt below, so a chip the keyboard just pressed is about to be destroyed
    // under its own focus: remember which one it was and hand focus back to its replacement, or
    // Tab would restart from the top of the document after every filter (issue #26)
    const active = document.activeElement;
    const refocus = active instanceof HTMLElement && this.groups.contains(active) ? { key: active.dataset['key'], value: active.dataset['value'] } : null;
    const restore: HTMLElement[] = [];
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
        if (refocus?.key === key && refocus.value === value) restore.push(b);
        return b;
      });
      this.groups.appendChild(h('div', { class: 'facet' }, h('div', { class: 'facet-name' }, FACET_LABELS[key], this.helpFor(key)), h('div', { class: 'opts' }, opts)));
    }
    restore[0]?.focus();
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
