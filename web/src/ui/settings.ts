/** Settings modal (⚙ / S). First option: the "listened" threshold; plus reset buttons. */
import { clear, h } from './dom';
import { DEFAULT_PREFS, type Prefs } from '../state/prefs';
import { COLUMNS, columnTitle } from './columns';
import { AUTO_NEXT_TIP, PRESERVE_TIP } from './tracklist';
import { isCompact } from '../config';
import { clearAllSiteData } from '../state/wipe';
import { FULLSCREEN_REFUSED, FULLSCREEN_UNSUPPORTED, fullscreenSupported, isFullscreen, toggleFullscreen } from './fullscreen';
import { activeElement, restoreFocus, trapTab } from './focus';

export interface SettingsCallbacks {
  onChange(p: Prefs): void;
  /**
   * Forget the listened marks of the track that is current *at the moment of the click* — playing
   * or stopped. The dialog deliberately holds no track of its own: it used to name one below the
   * button, which froze at the moment the dialog opened and then lied as soon as the track
   * changed underneath it (the window keymap steps tracks with [ and ] through an open dialog).
   */
  onResetTrack(): void;
  onResetAll(): void;
  onResetFont(): void;
  /** where focus goes when the dialog closes and the control that opened it is gone */
  onClose?(): void;
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
  private opener: HTMLElement | null = null;

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
        return;
      }
      // Tab wraps inside the box: at the window it is A/B, and focus that reaches <body> puts
      // every global shortcut back in charge behind an aria-modal dialog (#34)
      trapTab(this.box, e);
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
    // toggle(true) rebuilds the dialog, which is what refresh() exists to avoid: only open it
    // when it is not already open, or a half-typed 'listened after' value goes with it
    if (!this.visible) this.toggle(true);
    this.refresh(problem);
    // on a short viewport (phone landscape) the Display hint is below the fold
    this.fullscreenHint?.scrollIntoView?.({ block: 'nearest' });
  }

  toggle(force?: boolean): void {
    const was = this.visible;
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (this.visible) {
      if (!was) this.opener = activeElement();
      this.render();
      // focus the close button, not the number field: a focused text input makes iOS zoom in
      (this.box.querySelector('.btn.close-settings') as HTMLButtonElement | null)?.focus({ preventScroll: true });
      return;
    }
    // a refusal is about one attempt, not about the browser: it must not outlive the dialog
    this.fullscreenNote = '';
    if (was) restoreFocus(this.opener, () => this.cb.onClose?.());
    this.opener = null;
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
    // Both track options live here at every window size; the Tracks caption only shows them when it fits.
    const flag = (key: 'autoNextTrack' | 'preserveTrackPosition', label: string, title: string): HTMLElement => {
      const box = h('input', { type: 'checkbox' }) as HTMLInputElement;
      box.checked = this.prefs[key];
      box.addEventListener('change', () => {
        this.prefs = { ...this.prefs, [key]: box.checked };
        this.cb.onChange(this.prefs);
      });
      return h('label', { class: 'preserve', title }, box, ` ${label}`);
    };
    // "this track" is resolved by the click, not by the render: the dialog does not know (or
    // cache) which track that is, so it stays right while tracks change underneath it.
    const resetTrack = h('button', { class: 'btn', type: 'button', title: 'Forget which variants you have listened to on the track that is current now' }, 'reset for this track');
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
    fullscreen.addEventListener('click', () => void toggleFullscreen().then((ok) => this.refresh(ok ? '' : fullscreenSupported() ? FULLSCREEN_REFUSED : FULLSCREEN_UNSUPPORTED)));
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
      // the settings scroll, the close button does not: on a short viewport it has to stay on
      // screen to be a way out, exactly like the keys and share dialogs (#33)
      h(
        'div',
        { class: 'settings-scroll' },
        h(
          'section',
          { class: 'setting' },
          // the glyph is its own span so the amiga theme can lift it onto the parens' centre (#41)
          h('label', null, 'Mark a variant as listened (', h('span', { class: 'listened-glyph' }, '●'), ') after ', input, ' s of playback'),
          range,
          h('div', { class: 'btnrow' }, resetTrack, resetAll),
        ),
        h(
          'section',
          { class: 'setting' },
          h('div', { class: 'setting-title' }, 'Tracks'),
          h(
            'div',
            { class: 'trackopts' },
            flag('autoNextTrack', 'Automatically step to next track', AUTO_NEXT_TIP),
            flag('preserveTrackPosition', 'Preserve track position', PRESERVE_TIP),
          ),
        ),
        h(
          'section',
          { class: 'setting' },
          h('div', { class: 'setting-title' }, 'Display'),
          h('div', { class: 'btnrow colfoot' }, fullscreen, fullscreenHint),
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
          h('div', { class: 'btnrow colfoot' }, defaultsBtn, h('span', { class: 'muted small' }, '# and name are always shown. Click a header to sort; again to reverse; a third time for catalog order — # restores it in one click.')),
          h('div', { class: 'btnrow colfoot fontfoot' }, resetFont, h('span', { class: 'muted small' }, 'Click or tap the title bar to cycle font selection (modern theme only)')),
        ),
      ),
      // both names are load-bearing: .footrow carries #40's theme spacing, .settings-foot the
      // dialog chrome added with the share/keymap dialogs (#30/#33)
      h('div', { class: 'btnrow footrow settings-foot' }, close, wipe),
    );
  }
}
