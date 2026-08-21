import { KEYMAP } from '../input/keyboard';
import { h } from './dom';

export class KeymapOverlay {
  readonly el: HTMLElement;
  visible = false;
  constructor() {
    const rows = KEYMAP.map(([k, d]) => h('tr', null, h('td', null, h('kbd', null, k)), h('td', null, d)));
    this.el = h('div', { class: 'overlay hidden', role: 'dialog', 'aria-labelledby': 'keymap-title' }, h('div', { class: 'overlay-box' }, h('h2', { id: 'keymap-title' }, 'keys'), h('table', { class: 'keymap' }, ...rows), h('p', { class: 'muted' }, 'Esc to close')));
    this.el.addEventListener('click', (e) => {
      if (e.target === this.el) this.toggle(false);
    });
  }
  toggle(force?: boolean): void {
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
  }
}
