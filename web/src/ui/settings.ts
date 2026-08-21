/** Settings modal (⚙ / S). First option: the "listened" threshold; plus reset buttons. */
import { clear, h } from './dom';
import { DEFAULT_PREFS, type Prefs } from '../state/prefs';
import { COLUMNS } from './columns';

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
      // focus the close button, not the number field: a focused text input makes iOS zoom in
      (this.box.querySelector('.btn.close-settings') as HTMLButtonElement | null)?.focus({ preventScroll: true });
    }
  }

  private render(): void {
    clear(this.box);
    const compact = typeof matchMedia !== 'undefined' && matchMedia('(max-width: 720px)').matches;
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
    const defaultsBtn = h('button', { class: 'btn', type: 'button', title: 'restore the default columns for this layout' }, 'defaults');
    defaultsBtn.addEventListener('click', () => {
      const field = compact ? 'mobileColumns' : 'columns';
      this.prefs = { ...this.prefs, [field]: [...DEFAULT_PREFS[field]] };
      this.cb.onChange(this.prefs);
      this.render();
    });
    const close = h('button', { class: 'btn close-settings', type: 'button' }, 'close');
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
      h(
        'section',
        { class: 'setting' },
        h('div', { class: 'setting-title' }, compact ? 'List columns (phone layout)' : 'List columns'),
        h(
          'div',
          { class: 'colgrid' },
          COLUMNS.filter((c) => !c.always).map((c) => {
            // the checkboxes edit the column set of the *current* layout (phone vs desktop)
            const field = compact ? 'mobileColumns' : 'columns';
            const box = h('input', { type: 'checkbox' }) as HTMLInputElement;
            box.checked = this.prefs[field].includes(c.key);
            box.addEventListener('change', () => {
              const cols = new Set(this.prefs[field]);
              if (box.checked) cols.add(c.key);
              else cols.delete(c.key);
              this.prefs = { ...this.prefs, [field]: COLUMNS.filter((x) => cols.has(x.key)).map((x) => x.key) };
              this.cb.onChange(this.prefs);
            });
            return h('label', { class: 'preserve', title: c.title }, box, ` ${c.label}`);
          }),
        ),
        h('div', { class: 'btnrow' }, defaultsBtn),
        h('p', { class: 'muted small' }, '# and name are always shown. Click a column header to sort; click again to reverse, a third time to restore the catalog order.'),
      ),
      h('div', { class: 'btnrow' }, close),
    );
  }
}
