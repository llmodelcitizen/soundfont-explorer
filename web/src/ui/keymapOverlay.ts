import { KEYMAP } from '../input/keyboard';
import { h } from './dom';
import { activeElement, restoreFocus, trapTab } from './focus';

export interface KeymapCallbacks {
  /** where focus goes when the screen closes and the control that opened it is gone */
  onClose?(): void;
}

export class KeymapOverlay {
  readonly el: HTMLElement;
  visible = false;
  private box: HTMLElement;
  private close: HTMLButtonElement;
  private opener: HTMLElement | null = null;
  constructor(private cb: KeymapCallbacks = {}) {
    const rows = KEYMAP.map(([k, d]) => h('tr', null, h('td', null, h('kbd', null, k)), h('td', null, d)));
    // a real button, styled like the one Settings has — not a line of prose (#33)
    this.close = h('button', { class: 'btn close-keymap', type: 'button' }, 'close') as HTMLButtonElement;
    this.close.addEventListener('click', () => this.toggle(false));
    this.box = h(
      'div',
      { class: 'overlay-box keys' },
      h('h2', { id: 'keymap-title' }, 'keys'),
      // the list scrolls, the button does not: it has to stay on screen to be an exit
      h('div', { class: 'keymap-scroll' }, h('table', { class: 'keymap' }, ...rows)),
      h('div', { class: 'btnrow keymap-foot' }, this.close),
    );
    this.el = h('div', { class: 'overlay hidden', role: 'dialog', 'aria-labelledby': 'keymap-title' }, this.box);
    this.el.addEventListener('click', (e) => {
      if (e.target === this.el) this.toggle(false);
    });
    // Space on the focused close button is the button's own activation, not the window's
    // play/pause: the global handler would preventDefault it away before the click fired.
    this.el.addEventListener('keydown', (e) => {
      if (e.key === ' ' && e.target === this.close) e.stopPropagation();
      // and Tab stays in the box: at the window it is A/B, which would swap the pinned variant
      // behind the open screen and leave focus with nowhere to go back to (#33)
      trapTab(this.box, e);
    });
  }
  toggle(force?: boolean): void {
    const was = this.visible;
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (this.visible) {
      if (!was) this.opener = activeElement();
      this.close.focus({ preventScroll: true });
      return;
    }
    // the focused button is now display:none — hand focus back rather than dropping it on <body>
    if (was) restoreFocus(this.opener, () => this.cb.onClose?.());
    this.opener = null;
  }
}
