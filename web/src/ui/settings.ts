/** Settings modal (⚙ / S). First option: the "listened" threshold; plus reset buttons. */
import { clear, h } from './dom';
import type { Prefs } from '../state/prefs';

export interface SettingsCallbacks {
  onChange(p: Prefs): void;
  onResetTrack(): void;
  onResetAll(): void;
  trackTitle(): string;
}

export class SettingsModal {
  readonly el: HTMLElement;
  visible = false;
  private box: HTMLElement;

  constructor(private prefs: Prefs, private cb: SettingsCallbacks) {
    this.box = h('div', { class: 'overlay-box settings', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'settings-title' });
    this.el = h('div', { class: 'overlay hidden' }, this.box);
    this.el.addEventListener('click', (e) => {
      if (e.target === this.el) this.toggle(false);
    });
    this.el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this.toggle(false);
        e.stopPropagation();
      }
    });
    this.render();
  }

  setPrefs(p: Prefs): void {
    this.prefs = p;
    this.render();
  }

  toggle(force?: boolean): void {
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (this.visible) {
      this.render();
      (this.box.querySelector('input') as HTMLInputElement | null)?.focus();
    }
  }

  private render(): void {
    clear(this.box);
    const input = h('input', { type: 'number', min: '0', max: '60', step: '0.5', value: String(this.prefs.listenedAfterS), class: 'num', 'aria-label': 'seconds' }) as HTMLInputElement;
    const range = h('input', { type: 'range', min: '0', max: '30', step: '0.5', value: String(Math.min(30, this.prefs.listenedAfterS)), 'aria-label': 'seconds' }) as HTMLInputElement;
    const commit = (v: number) => {
      if (!Number.isFinite(v) || v < 0) return;
      v = Math.min(60, v);
      this.prefs = { ...this.prefs, listenedAfterS: v };
      input.value = String(v);
      range.value = String(Math.min(30, v));
      this.cb.onChange(this.prefs);
    };
    input.addEventListener('change', () => commit(Number(input.value)));
    range.addEventListener('input', () => commit(Number(range.value)));
    const resetTrack = h('button', { class: 'btn', type: 'button' }, `reset for this track`);
    resetTrack.addEventListener('click', () => this.cb.onResetTrack());
    const resetAll = h('button', { class: 'btn', type: 'button' }, 'reset for all tracks');
    resetAll.addEventListener('click', () => {
      if (confirm('Forget which variants you have listened to, for every track?')) this.cb.onResetAll();
    });
    const close = h('button', { class: 'btn', type: 'button' }, 'close');
    close.addEventListener('click', () => this.toggle(false));
    this.box.append(
      h('h2', { id: 'settings-title' }, 'settings'),
      h(
        'section',
        { class: 'setting' },
        h('label', null, 'Mark a variant as listened (●) after ', input, ' s of playback'),
        range,
        h('p', { class: 'muted' }, 'Only time the variant is actually audible counts. Remembered per track in this browser.'),
        h('div', { class: 'btnrow' }, resetTrack, resetAll),
        h('p', { class: 'muted small' }, `current track: ${this.cb.trackTitle()}`),
      ),
      h('div', { class: 'btnrow' }, close),
    );
  }
}
