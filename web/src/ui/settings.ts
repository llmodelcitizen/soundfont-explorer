/** Settings modal (⚙ / S). First option: the "listened" threshold; plus reset buttons. */
import { clear, h } from './dom';
import { DEFAULT_PREFS, type Prefs } from '../state/prefs';
import { COLUMNS, columnTitle } from './columns';
import { isCompact } from '../config';
import { clearAllSiteData } from '../state/wipe';
import { FULLSCREEN_UNSUPPORTED, fullscreenSupported, isFullscreen, toggleFullscreen } from './fullscreen';

export interface SettingsCallbacks {
  onChange(p: Prefs): void;
  onResetTrack(): void;
  onResetAll(): void;
  onResetFont(): void;
  trackTitle(): string;
}

/** what the Display hint says when nothing has gone wrong */
const FULLSCREEN_HINT = 'Fill the screen with the player. Shortcut: Shift + F (Esc leaves it).';

export class SettingsModal {
  readonly el: HTMLElement;
  visible = false;
  private box: HTMLElement;
  private fullscreenBtn: HTMLButtonElement | null = null;
  private fullscreenHint: HTMLElement | null = null;
  /** why the last full-screen attempt did not happen, shown in place of the hint */
  private fullscreenNote = '';

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

  /**
   * Repaint just the full-screen control, whose label follows the document's own state. Not a
   * full render(): a `fullscreenchange` can arrive at any moment (Esc, F11, another surface) and
   * rebuilding the dialog would drop the focus and any half-typed "listened after" value.
   */
  refresh(note = this.fullscreenNote): void {
    this.fullscreenNote = note;
    const can = fullscreenSupported();
    const btn = this.fullscreenBtn;
    if (btn) {
      btn.textContent = isFullscreen() ? 'leave full screen' : 'full screen';
      btn.disabled = !can;
      btn.title = can ? 'fill the screen (Shift + F)' : FULLSCREEN_UNSUPPORTED;
    }
    const hint = this.fullscreenHint;
    if (hint) {
      hint.textContent = note || (can ? FULLSCREEN_HINT : FULLSCREEN_UNSUPPORTED);
      hint.classList.toggle('warn', !!note);
    }
  }

  /** show why full screen did not happen, where the explanation already lives (#34) */
  reportFullscreen(problem: string): void {
    this.toggle(true);
    this.refresh(problem);
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
    const compact = isCompact();
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
    const defaultsBtn = h('button', { class: 'btn colaction', type: 'button', title: 'restore the default columns for this layout' }, 'defaults');
    defaultsBtn.addEventListener('click', () => {
      const field = compact ? 'mobileColumns' : 'columns';
      this.prefs = { ...this.prefs, [field]: [...DEFAULT_PREFS[field]] };
      this.cb.onChange(this.prefs);
      this.render();
    });
    const resetFont = h('button', { class: 'btn colaction', type: 'button', title: 'restore the default Modern font' }, 'reset font');
    resetFont.addEventListener('click', () => this.cb.onResetFont());
    const canFullscreen = fullscreenSupported();
    const fullscreen = h('button', { class: 'btn fullscreen-btn', type: 'button', disabled: !canFullscreen, title: canFullscreen ? 'fill the screen (Shift + F)' : FULLSCREEN_UNSUPPORTED }, isFullscreen() ? 'leave full screen' : 'full screen') as HTMLButtonElement;
    this.fullscreenBtn = fullscreen;
    // a rejected request (no user gesture, a permissions policy) resolves false instead of throwing
    fullscreen.addEventListener('click', () => void toggleFullscreen().then((ok) => this.refresh(ok ? '' : FULLSCREEN_UNSUPPORTED)));
    const fullscreenHint = h('span', { class: 'muted small' }, this.fullscreenNote || (canFullscreen ? FULLSCREEN_HINT : FULLSCREEN_UNSUPPORTED));
    if (this.fullscreenNote) fullscreenHint.classList.add('warn');
    this.fullscreenHint = fullscreenHint;
    const close = h('button', { class: 'btn close-settings', type: 'button' }, 'close');
    close.addEventListener('click', () => this.toggle(false));
    const wipe = h('button', { class: 'btn danger', type: 'button', title: 'Forget everything this site stored in this browser (settings, favorites, listened marks, pane sizes) and reload' }, 'clear all site data');
    wipe.addEventListener('click', () => {
      if (confirm('Clear everything this site stored in this browser — settings, favorites, listened marks, pane sizes — and reload?')) void clearAllSiteData();
    });
    this.box.append(
      h('h2', { id: 'settings-title' }, 'settings'),
      h(
        'section',
        { class: 'setting' },
        h('label', null, 'Mark a variant as listened (●) after ', input, ' s of playback'),
        range,
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
            return h('label', { class: `preserve col-${c.key}`, title: columnTitle(c) }, box, ` ${c.label}`);
          }),
        ),
        h('div', { class: 'btnrow colfoot' }, defaultsBtn, h('span', { class: 'muted small' }, '# and name are always shown. Click a header to sort; again to reverse; a third time for catalog order.')),
        h('div', { class: 'btnrow colfoot fontfoot' }, resetFont, h('span', { class: 'muted small' }, 'Click or tap the title bar to cycle font selection (modern theme only)')),
      ),
      h(
        'section',
        { class: 'setting' },
        h('div', { class: 'setting-title' }, 'Display'),
        h('div', { class: 'btnrow colfoot' }, fullscreen, fullscreenHint),
      ),
      h('div', { class: 'btnrow' }, close, wipe),
    );
  }
}
