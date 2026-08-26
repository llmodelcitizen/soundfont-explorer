/** Desktop track list (right pane, above Now Playing). Mobile keeps the header dropdown.
 *
 * Tracks are folded into one collapsible folder per directory `path` (top-level tracks
 * first, unfoldered — every pre-`path` songs.json renders exactly as before). Folder
 * open/closed state persists in localStorage; stepping with [ ] follows this displayed
 * logical order, and selecting a track inside a closed folder opens it. The caption's collapse
 * gadget shuts every open folder at once, store and all.
 */
import type { StatusKind } from '../audio/engine';
import { songTitle, type SongEntry } from '../contracts/songs';
import { safeStorage } from '../state/storage';
import { clear, h } from './dom';

const OPEN_KEY = 'sfp.folders.v1';

export const AUTO_NEXT_TIP = 'This is only useful when the LOOP button is not activated.';
export const PRESERVE_TIP = 'When checked, each track resumes from its own previous position. When unchecked, tracks start from the beginning.';
const COLLAPSE_ALL_LABEL = 'collapse all folders';

/**
 * The collapse-all gadget's drawing: two solid triangles folding onto a rule — everything closing
 * down to one line — in the same solid language as the ▾/▸ twists it shuts, and in the 16px box
 * and 24 viewBox the transport glyphs use (src/ui/icons.ts) so the two weigh the same. One asset
 * serves all three themes because it is filled with `currentColor`: it takes Modern's ink, the
 * Win95 caption bar's white and Workbench's black off the caption it sits in, with nothing
 * per-theme to keep in step. Filled, not stroked like the header gadgets — at 16px their 2px
 * stroke lands on 1.3 device pixels, and the two chevrons that would draw this blur into an X in
 * the very themes whose whole look is crisp pixels. The drawing is aria-hidden; the button around
 * it carries the name.
 */
const COLLAPSE_ICON =
  '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false" fill="currentColor">' +
  // inwards, onto the rule: the same two triangles pointing outwards is the drawing for "expand"
  '<path d="M4.5 2.5h15L12 9.5z"/><rect x="4.5" y="11" width="15" height="2"/><path d="M4.5 21.5h15L12 14.5z"/></svg>';

function groupedTracks(songs: SongEntry[]): { root: SongEntry[]; folders: [string, SongEntry[]][] } {
  const root = songs.filter((s) => !s.path);
  const byPath = new Map<string, SongEntry[]>();
  for (const s of songs) {
    if (s.path) (byPath.get(s.path) ?? byPath.set(s.path, []).get(s.path)!).push(s);
  }
  return { root, folders: [...byPath.entries()].sort(([a], [b]) => a.localeCompare(b)) };
}

/** The logical order shown by the Tracks pane, independent of which folders are collapsed. */
export function trackOrder(songs: SongEntry[]): SongEntry[] {
  const { root, folders } = groupedTracks(songs);
  return [...root, ...folders.flatMap(([, tracks]) => tracks)];
}

export function adjacentTrackId(songs: SongEntry[], current: string, delta: number): string | undefined {
  const ids = trackOrder(songs).map((s) => s.id);
  if (!ids.length) return undefined;
  const i = Math.max(0, ids.indexOf(current));
  return ids[(i + delta + ids.length) % ids.length];
}

/**
 * The track to step to when the engine reports the end of the current one, or undefined when
 * nothing should move: any other status, LOOP on (the song never ends), the preference off, or
 * the end of the list. Unlike `[` and `]` this does not wrap: stepping the user did not ask for
 * has to stop somewhere, or a tab left open walks the whole catalog and then plays it again for
 * ever, fetching every set and every audio segment each time round.
 */
export function autoAdvanceTarget(
  songs: SongEntry[],
  current: string,
  kind: StatusKind,
  opts: { loop: boolean; autoNext: boolean },
): string | undefined {
  if (kind !== 'ended' || opts.loop || !opts.autoNext) return undefined;
  const ids = trackOrder(songs).map((s) => s.id);
  const i = ids.indexOf(current);
  return i < 0 ? undefined : ids[i + 1];
}

export function trackMetadata(s: Pick<SongEntry, 'duration_s' | 'variant_count' | 'composer'>): string {
  const mm = Math.floor(s.duration_s / 60);
  const ss = String(Math.round(s.duration_s % 60)).padStart(2, '0');
  const composer = s.composer?.trim();
  return [`${mm}:${ss}`, `${s.variant_count} variants`, composer?.toLowerCase() === 'unknown' ? '' : composer ?? ''].filter(Boolean).join(' · ');
}

/** one of the two track options: its current value and where a click on it goes */
export interface TrackToggle {
  value: boolean;
  onChange: (v: boolean) => void;
}

/**
 * A caption toggle's text in two lengths. Both toggles fit the default split on a desktop only
 * in the short wording; the full wording appears on a wide pane, and `title` carries the whole
 * explanation either way. Settings always spells both options out in full.
 */
function captionLabel(full: string, short: string): HTMLElement {
  return h('span', { class: 'toglabel' }, h('span', { class: 'lbl-full' }, ` ${full}`), h('span', { class: 'lbl-short' }, ` ${short}`));
}

export class TrackList {
  readonly el: HTMLElement;
  private rows = new Map<string, HTMLElement>();
  private rowOrder: HTMLElement[] = [];
  private body: HTMLElement;
  private head: HTMLElement;
  private songs: SongEntry[] = [];
  private current = '';
  private open = new Set<string>();
  private observer: ResizeObserver | null = null;
  private stopWatchingFonts: (() => void) | null = null;
  private disposed = false;

  readonly autoNextBox: HTMLInputElement;
  readonly preserveBox: HTMLInputElement;
  readonly collapseBtn: HTMLButtonElement;

  constructor(
    songs: SongEntry[],
    current: string,
    private readonly onPick: (id: string) => void,
    toggles: { autoNext: TrackToggle; preserve: TrackToggle },
  ) {
    try {
      // the try still guards new Set(): a hand-edited store can hold something un-iterable
      this.open = new Set(safeStorage.getJson<string[]>(OPEN_KEY) ?? []);
    } catch { /* fresh */ }
    this.body = h('div', { class: 'track-rows', role: 'listbox', 'aria-label': 'tracks' });
    this.autoNextBox = this.toggle('auto-next-track', toggles.autoNext);
    this.preserveBox = this.toggle('preserve-pos', toggles.preserve);
    this.collapseBtn = this.collapseAll();
    this.head = h(
      'div',
      { class: 'np-head' },
      h('span', { class: 'np-title' }, `${songs.length} tracks`),
      this.collapseBtn,
      h('span', { class: 'muted small' }, '· [ ] to step'),
      h('span', { class: 'spacer' }),
      // Settings carries the same two options at every window size; here they only fit sometimes.
      h(
        'span',
        { class: 'track-toggles' },
        h('label', { class: 'preserve', for: 'auto-next-track', title: AUTO_NEXT_TIP }, this.autoNextBox, captionLabel('Automatically step to next track', 'Auto-next')),
        h('label', { class: 'preserve', for: 'preserve-pos', title: PRESERVE_TIP }, this.preserveBox, captionLabel('Preserve track position', 'Preserve position')),
      ),
    );
    this.el = h('section', { class: 'tracks' }, this.head, this.body);
    this.watchCaptionWidth();
    this.setSongs(songs, current);
  }

  /**
   * Shut every open folder in one click.
   *
   * Disabled — not hidden — while there is nothing open, which covers both empty cases: a
   * catalogue with no folders at all (any pre-`path` songs.json) and folders that are all closed
   * already. Hiding it would take the gadget in and out of the caption every time a folder is
   * opened, and the caption is a measured fit (see fitCaption): its content width must not change
   * under it for reasons that have nothing to do with the pane. `disabled` also says out loud what
   * a silent no-op only implies — a screen reader reads the button as unavailable, and the pointer
   * gets no click at all — while the handler still refuses the work, since a programmatic click
   * ignores the attribute.
   */
  private collapseAll(): HTMLButtonElement {
    const btn = h('button', { class: 'collapse-all', type: 'button', title: COLLAPSE_ALL_LABEL, 'aria-label': COLLAPSE_ALL_LABEL }) as HTMLButtonElement;
    btn.innerHTML = COLLAPSE_ICON;
    btn.addEventListener('click', () => {
      if (btn.disabled) return; // the handler refuses exactly what the attribute refuses
      this.open.clear();
      // save before rendering: the pane and `sfp.folders.v1` must never disagree, or a reload
      // re-opens folders the user just watched close
      this.saveOpen();
      this.render();
      btn.blur(); // as the caption's checkboxes do — the window keymap owns the keyboard again
    });
    return btn;
  }

  private toggle(id: string, toggle: TrackToggle): HTMLInputElement {
    const box = h('input', { type: 'checkbox', id }) as HTMLInputElement;
    box.checked = toggle.value;
    box.addEventListener('change', () => {
      toggle.onChange(box.checked);
      box.blur();
    });
    return box;
  }

  /**
   * Fit the caption to its pane in four steps: full labels, short labels, no collapse gadget, then
   * no toggles at all (a dragged-in split, a small window, iOS mobile-landscape — Settings still
   * has both). The second step is what keeps them on show at ordinary laptop widths: the full
   * wording needs ~490 px of caption in the Modern face and ~585 px in Topaz, where the default
   * split gives about 410 px at 1280×800, so all-or-nothing would hide them for most desktop users.
   *
   * The gadget goes before either option does because it is the one control here with a way round
   * it — folders still close one at a time — where the two options exist nowhere else on the
   * screen, and because Topaz has only about 6 px to spare at that same default split, so a gadget
   * that refused to move would cost Amiga users both toggles. Once the toggles have gone the
   * caption has ~200 px back and the gadget returns (the stylesheet's `.cramped` rule): it was
   * never what was squeezing the row.
   *
   * Measuring beats a breakpoint: the pane width is the user's, not the viewport's. Neither step
   * can change the caption's own box — it is a fixed-height row stretched to the pane, and
   * `.tracks { min-width: 0 }` makes the pane set the caption's width rather than the other way
   * round — so this never feeds itself a new observation, and the overflow it looks for can
   * actually happen.
   */
  private fitCaption(): void {
    if (this.disposed) return;
    this.head.classList.remove('short', 'tight', 'cramped');
    if (!this.head.clientWidth || !this.overflowing()) return;
    this.head.classList.add('short');
    if (!this.overflowing()) return;
    this.head.classList.add('tight');
    if (this.overflowing()) this.head.classList.add('cramped');
  }

  private overflowing(): boolean {
    return this.head.scrollWidth > this.head.clientWidth;
  }

  /**
   * Re-take the decision whenever the caption's *content* width can have changed at a fixed box
   * width: a webfont arriving after the first observation (Topaz and IBM Plex both swap in late),
   * a theme switch, a Modern font cycle. The ResizeObserver alone only sees the box.
   */
  refit(): void {
    this.fitCaption();
  }

  private watchCaptionWidth(): void {
    if (typeof ResizeObserver === 'undefined') return;
    this.observer = new ResizeObserver(() => this.fitCaption());
    this.observer.observe(this.head);
    const fonts = typeof document !== 'undefined' ? document.fonts : undefined;
    if (!fonts) return;
    const onLoaded = () => this.fitCaption();
    fonts.addEventListener('loadingdone', onLoaded);
    this.stopWatchingFonts = () => fonts.removeEventListener('loadingdone', onLoaded);
    void fonts.ready.then(onLoaded, () => undefined);
  }

  /** Drop the observers before the pane is replaced: a song switch builds a whole new TrackList. */
  dispose(): void {
    this.disposed = true;
    this.observer?.disconnect();
    this.observer = null;
    this.stopWatchingFonts?.();
    this.stopWatchingFonts = null;
  }

  setSongs(songs: SongEntry[], current: string): void {
    this.songs = songs;
    this.current = current;
    const path = songs.find((s) => s.id === current)?.path;
    if (path && !this.open.has(path)) {
      this.open.add(path);
      this.saveOpen();
    }
    this.render();
  }

  setCurrent(id: string): void {
    this.current = id;
    const s = this.songs.find((x) => x.id === id);
    if (s?.path && !this.open.has(s.path)) {
      this.open.add(s.path);
      this.saveOpen();
      this.render();
      return;
    }
    this.applyCurrent();
  }

  get scrollTop(): number {
    return this.body.scrollTop;
  }

  /** Restore a rebuilt pane, moving only when needed to retain one track above and below. */
  restoreView(scrollTop: number): void {
    if (!this.body.clientHeight) return; // hidden mobile track pane
    this.body.scrollTop = scrollTop;
    const row = this.rows.get(this.current);
    const i = row ? this.rowOrder.indexOf(row) : -1;
    if (!row || i < 0) return;
    const first = this.rowOrder[Math.max(0, i - 1)]!;
    const last = this.rowOrder[Math.min(this.rowOrder.length - 1, i + 1)]!;
    const viewport = this.body.getBoundingClientRect();
    const contextTop = first.getBoundingClientRect().top - viewport.top + this.body.scrollTop;
    const contextBottom = last.getBoundingClientRect().bottom - viewport.top + this.body.scrollTop;
    if (contextTop < this.body.scrollTop) this.body.scrollTop = contextTop;
    else if (contextBottom > this.body.scrollTop + this.body.clientHeight) {
      this.body.scrollTop = contextBottom - this.body.clientHeight;
    }
  }

  private saveOpen(): void {
    safeStorage.setJson(OPEN_KEY, [...this.open]);
  }

  private render(): void {
    clear(this.body);
    this.rows.clear();
    this.rowOrder = [];
    const { root, folders } = groupedTracks(this.songs);
    // live only while a folder that is actually on screen is open: `this.open` can still name a
    // folder this catalogue no longer has (the store outlives a songs.json), and a gadget offering
    // to close what nobody can see is a lie. Clicking it does clear those stale names too.
    this.collapseBtn.disabled = !folders.some(([path]) => this.open.has(path));
    for (const s of root) this.body.appendChild(this.row(s));
    for (const [path, files] of folders) {
      const isOpen = this.open.has(path);
      const head = h(
        'div',
        { class: 'track-folder', role: 'presentation', title: path },
        h('span', { class: 'folder-twist' }, isOpen ? '▾' : '▸'),
        h('span', { class: 'folder-name' }, path),
        h('span', { class: 'folder-count' }, String(files.length)),
      );
      head.addEventListener('click', () => {
        if (this.open.has(path)) this.open.delete(path);
        else this.open.add(path);
        this.saveOpen();
        this.render();
      });
      this.body.appendChild(head);
      if (!isOpen) continue;
      for (const s of files) this.body.appendChild(this.row(s, true));
    }
    this.applyCurrent();
  }

  private row(s: SongEntry, inFolder = false): HTMLElement {
    const row = h(
      'div',
      { class: inFolder ? 'track in-folder' : 'track', role: 'option', dataset: { id: s.id }, title: s.path ? `${s.path}/ — ${songTitle(s)}` : songTitle(s) },
      h('span', { class: 'track-title' }, s.title),
      h('span', { class: 'track-meta' }, trackMetadata(s)),
    );
    row.addEventListener('click', () => this.onPick(s.id));
    this.rows.set(s.id, row);
    this.rowOrder.push(row);
    return row;
  }

  private applyCurrent(): void {
    for (const [sid, row] of this.rows) {
      row.classList.toggle('current', sid === this.current);
      if (sid === this.current) {
        row.setAttribute('aria-selected', 'true');
      } else row.removeAttribute('aria-selected');
    }
  }
}
