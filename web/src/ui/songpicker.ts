import type { SongEntry } from '../contracts/songs';
import { h } from './dom';

export class SongPicker {
  readonly el: HTMLSelectElement;
  constructor(songs: SongEntry[], current: string, onChange: (id: string) => void) {
    this.el = h('select', { class: 'songpicker', 'aria-label': 'song', title: 'song ( [ / ] )' });
    for (const s of songs) {
      const o = h('option', { value: s.id }, `${s.title}${s.composer ? ' — ' + s.composer : ''}  (${s.variant_count})`);
      if (s.id === current) o.selected = true;
      this.el.appendChild(o);
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
