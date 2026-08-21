/** Desktop track list (right pane, above Now Playing). Mobile keeps the header dropdown. */
import type { SongEntry } from '../contracts/songs';
import { clear, h } from './dom';

export class TrackList {
  readonly el: HTMLElement;
  private rows = new Map<string, HTMLElement>();
  private body: HTMLElement;

  constructor(songs: SongEntry[], current: string, private readonly onPick: (id: string) => void) {
    this.body = h('div', { class: 'track-rows', role: 'listbox', 'aria-label': 'tracks' });
    this.el = h('section', { class: 'tracks' }, h('div', { class: 'np-head' }, h('span', { class: 'np-title' }, 'tracks'), h('span', { class: 'muted small' }, `${songs.length} · [ ] to step`)), this.body);
    this.setSongs(songs, current);
  }

  setSongs(songs: SongEntry[], current: string): void {
    clear(this.body);
    this.rows.clear();
    for (const s of songs) {
      const mm = Math.floor(s.duration_s / 60);
      const ss = String(Math.round(s.duration_s % 60)).padStart(2, '0');
      const row = h(
        'div',
        { class: 'track', role: 'option', dataset: { id: s.id }, title: `${s.title}${s.composer ? ' — ' + s.composer : ''}` },
        h('span', { class: 'track-title' }, s.title),
        h('span', { class: 'track-meta' }, [`${mm}:${ss}`, `${s.variant_count} variants`, s.composer ?? ''].filter(Boolean).join(' · ')),
      );
      row.addEventListener('click', () => this.onPick(s.id));
      this.rows.set(s.id, row);
      this.body.appendChild(row);
    }
    this.setCurrent(current);
  }

  setCurrent(id: string): void {
    for (const [sid, row] of this.rows) {
      row.classList.toggle('current', sid === id);
      if (sid === id) {
        row.setAttribute('aria-selected', 'true');
        row.scrollIntoView({ block: 'nearest' });
      } else row.removeAttribute('aria-selected');
    }
  }
}
