import { KEYMAP } from '../input/keyboard';
import { h } from './dom';

export class KeymapOverlay {
  readonly el: HTMLElement;
  visible = false;
  private close: HTMLButtonElement;
  constructor() {
    const rows = KEYMAP.map(([k, d]) => h('tr', null, h('td', null, h('kbd', null, k)), h('td', null, d)));
    // a real button, styled like the one Settings has — not a line of prose (#33)
    this.close = h('button', { class: 'btn close-keymap', type: 'button' }, 'close') as HTMLButtonElement;
    this.close.addEventListener('click', () => this.toggle(false));
    this.el = h(
      'div',
      { class: 'overlay hidden', role: 'dialog', 'aria-labelledby': 'keymap-title' },
      h('div', { class: 'overlay-box' }, h('h2', { id: 'keymap-title' }, 'keys'), h('table', { class: 'keymap' }, ...rows), h('div', { class: 'btnrow keymap-foot' }, this.close)),
    );
    this.el.addEventListener('click', (e) => {
      if (e.target === this.el) this.toggle(false);
    });
  }
  toggle(force?: boolean): void {
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (this.visible) this.close.focus({ preventScroll: true });
  }
}
