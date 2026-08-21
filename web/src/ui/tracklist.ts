/** Desktop track list (right pane, above Now Playing). Mobile keeps the header dropdown. */
import { songTitle, type SongEntry } from '../contracts/songs';
import { clear, h } from './dom';

export class TrackList {
  readonly el: HTMLElement;
  private rows = new Map<string, HTMLElement>();
  private body: HTMLElement;

  readonly preserveBox: HTMLInputElement;

  constructor(songs: SongEntry[], current: string, private readonly onPick: (id: string) => void, preserve: { value: boolean; onChange: (v: boolean) => void }) {
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
    clear(this.body);
    this.rows.clear();
    for (const s of songs) {
      const mm = Math.floor(s.duration_s / 60);
      const ss = String(Math.round(s.duration_s % 60)).padStart(2, '0');
      const row = h(
        'div',
        { class: 'track', role: 'option', dataset: { id: s.id }, title: songTitle(s) },
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
