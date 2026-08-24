/** Desktop track list (right pane, above Now Playing). Mobile keeps the header dropdown.
 *
 * Tracks are folded into one collapsible folder per directory `path` (top-level tracks
 * first, unfoldered — every pre-`path` songs.json renders exactly as before). Folder
 * open/closed state persists in localStorage; stepping with [ ] follows this displayed
 * logical order, and selecting a track inside a closed folder opens it.
 */
import { songTitle, type SongEntry } from '../contracts/songs';
import { clear, h } from './dom';

const OPEN_KEY = 'sfp.folders.v1';

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

export function trackMetadata(s: Pick<SongEntry, 'duration_s' | 'variant_count' | 'composer'>): string {
  const mm = Math.floor(s.duration_s / 60);
  const ss = String(Math.round(s.duration_s % 60)).padStart(2, '0');
  const composer = s.composer?.trim();
  return [`${mm}:${ss}`, `${s.variant_count} variants`, composer?.toLowerCase() === 'unknown' ? '' : composer ?? ''].filter(Boolean).join(' · ');
}

export class TrackList {
  readonly el: HTMLElement;
  private rows = new Map<string, HTMLElement>();
  private rowOrder: HTMLElement[] = [];
  private body: HTMLElement;
  private songs: SongEntry[] = [];
  private current = '';
  private open = new Set<string>();

  readonly preserveBox: HTMLInputElement;

  constructor(songs: SongEntry[], current: string, private readonly onPick: (id: string) => void, preserve: { value: boolean; onChange: (v: boolean) => void }) {
    try {
      this.open = new Set(JSON.parse(localStorage.getItem(OPEN_KEY) ?? '[]') as string[]);
    } catch { /* fresh */ }
    this.body = h('div', { class: 'track-rows', role: 'listbox', 'aria-label': 'tracks' });
    this.preserveBox = h('input', { type: 'checkbox', id: 'preserve-pos' }) as HTMLInputElement;
    this.preserveBox.checked = preserve.value;
    this.preserveBox.addEventListener('change', () => {
      preserve.onChange(this.preserveBox.checked);
      this.preserveBox.blur();
    });
    const tip = 'When checked, each track resumes from its own previous position. When unchecked, tracks start from the beginning.';
    const label = h('label', { class: 'preserve', for: 'preserve-pos', title: tip }, this.preserveBox, ' Preserve track position');
    this.el = h(
      'section',
      { class: 'tracks' },
      h('div', { class: 'np-head' }, h('span', { class: 'np-title' }, 'tracks'), h('span', { class: 'muted small' }, `${songs.length} · [ ] to step`), h('span', { class: 'spacer' }), label),
      this.body,
    );
    this.setSongs(songs, current);
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
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify([...this.open]));
    } catch { /* private mode */ }
  }

  private render(): void {
    clear(this.body);
    this.rows.clear();
    this.rowOrder = [];
    const { root, folders } = groupedTracks(this.songs);
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
