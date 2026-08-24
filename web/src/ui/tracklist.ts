/** Desktop track list (right pane, above Now Playing). Mobile keeps the header dropdown.
 *
 * Tracks are folded into one collapsible folder per directory `path` (top-level tracks
 * first, unfoldered — every pre-`path` songs.json renders exactly as before). Folder
 * open/closed state persists in localStorage; stepping with [ ] stays flat songs.json
 * order (app.ts), and selecting a track inside a closed folder opens it.
 */
import { songTitle, type SongEntry } from '../contracts/songs';
import { clear, h } from './dom';

const OPEN_KEY = 'sfp.folders.v1';

export class TrackList {
  readonly el: HTMLElement;
  private rows = new Map<string, HTMLElement>();
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
    const tip = 'When unchecked, always start from the beginning after stepping to a new track.';
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

  private saveOpen(): void {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify([...this.open]));
    } catch { /* private mode */ }
  }

  private render(): void {
    clear(this.body);
    this.rows.clear();
    const root = this.songs.filter((s) => !s.path);
    const byPath = new Map<string, SongEntry[]>();
    for (const s of this.songs) {
      if (s.path) (byPath.get(s.path) ?? byPath.set(s.path, []).get(s.path)!).push(s);
    }
    for (const s of root) this.body.appendChild(this.row(s));
    for (const path of [...byPath.keys()].sort()) {
      const files = byPath.get(path)!;
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
    const mm = Math.floor(s.duration_s / 60);
    const ss = String(Math.round(s.duration_s % 60)).padStart(2, '0');
    const row = h(
      'div',
      { class: inFolder ? 'track in-folder' : 'track', role: 'option', dataset: { id: s.id }, title: s.path ? `${s.path}/ — ${songTitle(s)}` : songTitle(s) },
      h('span', { class: 'track-title' }, s.title),
      h('span', { class: 'track-meta' }, [`${mm}:${ss}`, `${s.variant_count} variants`, s.composer ?? ''].filter(Boolean).join(' · ')),
    );
    row.addEventListener('click', () => this.onPick(s.id));
    this.rows.set(s.id, row);
    return row;
  }

  private applyCurrent(): void {
    for (const [sid, row] of this.rows) {
      row.classList.toggle('current', sid === this.current);
      if (sid === this.current) {
        row.setAttribute('aria-selected', 'true');
        row.scrollIntoView({ block: 'nearest' });
      } else row.removeAttribute('aria-selected');
    }
  }
}
