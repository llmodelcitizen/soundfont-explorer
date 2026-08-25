import { KEYMAP } from '../input/keyboard';
import { h } from './dom';

export interface KeymapCallbacks {
  /** where focus goes when the screen closes — the button it was on is about to be hidden */
  onClose?(): void;
}

export class KeymapOverlay {
  readonly el: HTMLElement;
  visible = false;
  private close: HTMLButtonElement;
  constructor(private cb: KeymapCallbacks = {}) {
    const rows = KEYMAP.map(([k, d]) => h('tr', null, h('td', null, h('kbd', null, k)), h('td', null, d)));
    // a real button, styled like the one Settings has — not a line of prose (#33)
    this.close = h('button', { class: 'btn close-keymap', type: 'button' }, 'close') as HTMLButtonElement;
    this.close.addEventListener('click', () => this.toggle(false));
    this.el = h(
      'div',
      { class: 'overlay hidden', role: 'dialog', 'aria-labelledby': 'keymap-title' },
      h(
        'div',
        { class: 'overlay-box keys' },
        h('h2', { id: 'keymap-title' }, 'keys'),
        // the list scrolls, the button does not: it has to stay on screen to be an exit
        h('div', { class: 'keymap-scroll' }, h('table', { class: 'keymap' }, ...rows)),
        h('div', { class: 'btnrow keymap-foot' }, this.close),
      ),
    );
    this.el.addEventListener('click', (e) => {
      if (e.target === this.el) this.toggle(false);
    });
    // Space on the focused close button is the button's own activation, not the window's
    // play/pause: the global handler would preventDefault it away before the click fired.
    this.el.addEventListener('keydown', (e) => {
      if (e.key === ' ' && e.target === this.close) e.stopPropagation();
    });
  }
  toggle(force?: boolean): void {
    const was = this.visible;
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (this.visible) this.close.focus({ preventScroll: true });
    // the focused button is now display:none — hand focus back rather than dropping it on <body>
    else if (was) this.cb.onClose?.();
  }
}
