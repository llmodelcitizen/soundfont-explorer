import { songTitle, type SongEntry } from '../contracts/songs';
import { h } from './dom';

export class SongPicker {
  readonly el: HTMLSelectElement;
  constructor(songs: SongEntry[], current: string, onChange: (id: string) => void) {
    this.el = h('select', { class: 'songpicker', 'aria-label': 'song', title: 'song ( [ / ] )' });
    const option = (s: SongEntry) => {
      const o = h('option', { value: s.id }, `${songTitle(s)}  (${s.variant_count})`);
      if (s.id === current) o.selected = true;
      return o;
    };
    // top-level tracks first (a pre-`path` songs.json is all top-level: flat as before),
    // then one <optgroup> per directory
    for (const s of songs) if (!s.path) this.el.appendChild(option(s));
    const byPath = new Map<string, SongEntry[]>();
    for (const s of songs) {
      if (s.path) (byPath.get(s.path) ?? byPath.set(s.path, []).get(s.path)!).push(s);
    }
    for (const path of [...byPath.keys()].sort()) {
      const g = h('optgroup', { label: path });
      for (const s of byPath.get(path)!) g.appendChild(option(s));
      this.el.appendChild(g);
    }
    this.el.addEventListener('change', () => {
      onChange(this.el.value);
      this.el.blur();
    });
  }
  set(id: string): void {
    this.el.value = id;
  }
}
