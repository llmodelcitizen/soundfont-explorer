/**
 * Share dialog (the ⤴ button, immediately left of ⚙): the page URL in a box tall enough to read
 * the whole link at once, a copy button, the option to carry the current position, and what every
 * URL parameter means. No social network anything — a link and a copy button (issue #30).
 */
import { URL_PARAM_HELP, URL_PARAM_ORDER, paramsIn, type ShareLinks, type UrlParam } from '../state/urlstate';
import { clear, h } from './dom';
import { activeElement, restoreFocus, trapTab } from './focus';

export interface ShareCallbacks {
  /** the links for the state as it stands right now — re-read before every copy */
  links(): ShareLinks;
  /** where focus goes when the dialog closes and the control that opened it is gone */
  onClose?(): void;
  /** overrides the pointer media query; only tests have any business passing this */
  coarsePointer?(): boolean;
}

/** a field a copy can be made from — the dialog's own textarea, or an <input> in a caller's DOM */
type CopyField = HTMLInputElement | HTMLTextAreaElement;

/** neither clipboard path worked, and there is a keyboard to fall back on */
export const COPY_FAILED_KEYS = 'could not copy — press ⌘/Ctrl + C';
/** the same failure where there is no ⌘ and no Ctrl: the platform's own long-press menu is the way */
export const COPY_FAILED_TOUCH = 'could not copy — touch and hold the link, then Copy';

/**
 * A finger rather than a mouse, asked by capability and never by user agent: `pointer: coarse`
 * describes the *primary* input, so a touchscreen laptop still counts as a desktop. Both mobile
 * platforms drag fat handles and a callout bar around any selection, which is why the copy button
 * highlights the link on a desktop only.
 */
export function coarsePointer(view: Window | null = typeof window === 'undefined' ? null : window): boolean {
  try {
    return view?.matchMedia?.('(pointer: coarse)')?.matches ?? false;
  } catch {
    return false; // no matchMedia to ask: assume a pointer, which is the side that highlights
  }
}

/**
 * The `t=` the given link carries, in seconds. The two links are the only place this dialog ever
 * sees the position, so the label reads it back out of one rather than asking for it twice.
 */
export function startSeconds(url: string): number {
  const q = url.indexOf('?');
  if (q < 0) return 0;
  const t = Number(new URLSearchParams(url.slice(q + 1)).get('t'));
  return Number.isFinite(t) && t > 0 ? t : 0;
}

/** `t=` is whole seconds, so the label says the same number back: m:ss, h:mm:ss past an hour */
export function fmtStart(seconds: number): string {
  const whole = Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds) : 0;
  const s = whole % 60;
  const m = Math.floor(whole / 60) % 60;
  const hours = Math.floor(whole / 3600);
  const mm = hours ? String(m).padStart(2, '0') : String(m);
  return `${hours ? `${hours}:` : ''}${mm}:${String(s).padStart(2, '0')}`;
}

/**
 * Put `text` on the clipboard: the async clipboard first, a selection copy behind it.
 *
 * The async clipboard is the only path that needs nothing selected, but it exists only in a
 * secure context (an http:// page has no `navigator.clipboard` at all) and can still be refused.
 * It is started *synchronously*, before anything here is awaited — iOS and Android grant the
 * write against the user gesture that is still running, and an `await` ahead of the call spends
 * that gesture. For the same reason the fallback runs synchronously when there is no async
 * clipboard to try, instead of a microtask later.
 *
 * The fallback is the shape iOS Safari actually honours: `select()` does nothing on a `readonly`
 * field, Safari only extends a selection into a form control that is content-editable, and
 * `execCommand('copy')` with an empty selection reports success while copying nothing. So the
 * field is made writable and editable for the length of the copy, a Range is laid over it, and
 * both are put straight back.
 */
export function copyText(field: CopyField, text: string): Promise<boolean> {
  let written: Promise<void> | null = null;
  try {
    written = navigator.clipboard?.writeText?.(text) ?? null;
  } catch {
    written = null; // an implementation that throws where it should have rejected
  }
  if (!written) return Promise.resolve(selectionCopy(field, text));
  return written.then(
    () => true,
    () => selectionCopy(field, text),
  );
}

/** the fallback half of copyText(): copies whatever the field holds, so the field gets `text` first */
function selectionCopy(field: CopyField, text: string): boolean {
  const doc = field.ownerDocument;
  const wasReadOnly = field.readOnly;
  try {
    if (field.value !== text) field.value = text;
    field.readOnly = false;
    field.setAttribute('contenteditable', 'true');
    field.focus({ preventScroll: true });
    try {
      const selection = doc.defaultView?.getSelection?.();
      const range = doc.createRange();
      range.selectNodeContents(field);
      selection?.removeAllRanges();
      selection?.addRange(range);
    } catch {
      /* a DOM that will not lay a Range over a form control: setSelectionRange still stands */
    }
    field.setSelectionRange?.(0, text.length);
    return doc.execCommand?.('copy') ?? false;
  } catch {
    return false;
  } finally {
    field.readOnly = wasReadOnly;
    field.removeAttribute('contenteditable');
  }
}

export class ShareDialog {
  /** how often the open dialog re-reads the links: the app moves on (position, song, theme) */
  static readonly SYNC_MS = 250;
  readonly el: HTMLElement;
  visible = false;
  private box: HTMLElement;
  private links: ShareLinks = { withTime: '', withoutTime: '' };
  /** the ⏱ checkbox: include the current position (reset every time the dialog opens) */
  private withTime = true;
  private field: HTMLTextAreaElement | null = null;
  private time: HTMLInputElement | null = null;
  private opt: HTMLElement | null = null;
  /** the m:ss inside the timestamp option's label — it keeps counting while the dialog is open */
  private at: HTMLElement | null = null;
  private note: HTMLElement | null = null;
  private copyBtn: HTMLButtonElement | null = null;
  /** what the field and the label are showing, so a re-sync that changes nothing touches nothing */
  private shownUrl: string | null = null;
  private shownSame = false;
  private shownStart = '';
  private timer: ReturnType<typeof setInterval> | null = null;
  private opener: HTMLElement | null = null;

  constructor(private cb: ShareCallbacks) {
    this.box = h('div', { class: 'overlay-box share', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'share-title' });
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
      // Tab wraps inside the box instead of walking out of it: focus that reaches <body> hands
      // the window keymap back its shortcuts, and Tab = A/B there means it cannot walk back in.
      if (trapTab(this.box, e)) return;
      // aria-modal means it: nothing else typed at an open dialog reaches the window keymap, which
      // would otherwise load another song or flip the theme behind it (Space, T, [, ]).
      e.stopPropagation();
    });
    this.render();
  }

  toggle(force?: boolean): void {
    const was = this.visible;
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (!this.visible) {
      this.stopSyncing();
      // the focused control is now display:none — hand focus back instead of dropping it on <body>
      if (was) restoreFocus(this.opener, () => this.cb.onClose?.());
      this.opener = null;
      return;
    }
    if (!was) this.opener = activeElement();
    this.withTime = true; // every share starts from the whole link; the checkbox takes the time off
    this.render();
    this.sync();
    // focus lands on the copy button, not on the link: an aria-modal dialog has to hold focus, and
    // a pre-selected field is a wall of highlight — worse, two drag handles on a phone (#30)
    this.focusCopy();
    this.startSyncing();
  }

  /** re-read the links: the app's state moves on (position, theme, song) while the dialog is open */
  private sync(): void {
    this.links = this.cb.links();
    // at the top of the track the two links are identical: an option that does nothing reads as broken
    const same = this.links.withTime === this.links.withoutTime;
    const url = this.url;
    // always the *live* position, even with the option off: it is what ticking the box would add
    const start = fmtStart(startSeconds(this.links.withTime));
    if (url === this.shownUrl && same === this.shownSame && start === this.shownStart) return;
    const moved = this.shownUrl !== null && url !== this.shownUrl;
    this.shownUrl = url;
    this.shownSame = same;
    this.shownStart = start;
    const field = this.field;
    if (field && field.value !== url) {
      // a whole-value selection survives the new text — ours from a copy, or a select-all of their
      // own, either way ⌘/Ctrl + C keeps copying what the field shows. A caret or a partial
      // selection is left where it is.
      const whole = field.ownerDocument.activeElement === field && field.selectionStart === 0 && field.selectionEnd === field.value.length && field.value.length > 0;
      field.value = url;
      if (whole) field.select();
    }
    if (this.at) this.at.textContent = start;
    const time = this.time;
    if (time) {
      time.disabled = same;
      // the box says what the link in the field carries, never a sticky preference of its own
      time.checked = this.includeTime;
    }
    this.opt?.classList.toggle('off', same);
    // 'copied' would be a lie about the link now in the field
    if (moved) this.setNote('');
    this.renderParams();
  }

  private startSyncing(): void {
    if (this.timer === null) this.timer = setInterval(() => this.sync(), ShareDialog.SYNC_MS);
  }

  private stopSyncing(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }

  /** the desktop copy affordance: the link highlights so it is obvious what landed on the clipboard */
  private selectField(): void {
    const field = this.field;
    if (!field) return;
    field.focus({ preventScroll: true });
    field.select();
  }

  /** undo whatever the fallback copy had to select, and put focus back on the button that was used */
  private clearSelection(): void {
    const field = this.field;
    field?.setSelectionRange?.(0, 0);
    field?.ownerDocument.defaultView?.getSelection?.()?.removeAllRanges?.();
    this.focusCopy();
  }

  private focusCopy(): void {
    this.copyBtn?.focus({ preventScroll: true });
  }

  private setNote(text: string, ok = true): void {
    if (!this.note) return;
    this.note.textContent = text;
    // a copy that did not happen has to look different from one that did: silence here is the bug
    this.note.classList.toggle('warn', !!text && !ok);
    this.note.classList.toggle('muted', !text || ok);
  }

  private get coarse(): boolean {
    return this.cb.coarsePointer?.() ?? coarsePointer();
  }

  /** the time is only on the link when it is asked for *and* it would change the link */
  private get includeTime(): boolean {
    return this.withTime && this.links.withTime !== this.links.withoutTime;
  }

  private get url(): string {
    return this.includeTime ? this.links.withTime : this.links.withoutTime;
  }

  private render(): void {
    clear(this.box);
    this.shownUrl = null; // nothing is on screen yet: the next sync() paints everything
    this.shownStart = '';
    // a textarea, not an input: a URL long enough to be worth sharing does not fit one line, and a
    // field you have to drag sideways through hides the half of the link that matters. cols=1 is
    // the size=1 trick — without it the 20-character intrinsic width alone made the box wider than
    // a 320px phone at 16px Fixedsys (win95, coarse pointer) (#30).
    const field = h('textarea', { class: 'share-url', rows: '3', cols: '1', wrap: 'soft', readonly: true, spellcheck: 'false', 'aria-label': 'link to this page' });
    field.value = this.url;
    this.field = field;
    const note = h('span', { class: 'muted small share-note', role: 'status' }, '');
    this.note = note;
    const copy = h('button', { class: 'btn primary share-copy', type: 'button', title: 'copy the link' }, 'copy');
    this.copyBtn = copy;
    copy.addEventListener('click', () => {
      this.sync(); // copy the link for where the app is now, not for where it was when this opened
      const text = this.url;
      // the highlight goes on before the copy: it shows what was taken, and it is also the only
      // state the execCommand fallback can copy from. Not on a phone — see coarsePointer().
      if (!this.coarse) this.selectField();
      void copyText(field, text).then((ok) => {
        this.setNote(ok ? 'copied' : this.coarse ? COPY_FAILED_TOUCH : COPY_FAILED_KEYS, ok);
        // the async path never touches the field: put the selection back, so the dialog keeps the
        // focus its aria-modal promises and ⌘C still works. On a phone, take back anything the
        // fallback had to select — bare handles over the link are not an affordance.
        if (this.coarse) this.clearSelection();
        else this.selectField();
      });
    });
    const time = h('input', { type: 'checkbox', class: 'share-time' }) as HTMLInputElement;
    time.checked = this.includeTime;
    this.time = time;
    time.addEventListener('change', () => {
      this.withTime = time.checked;
      this.setNote('');
      this.sync();
    });
    const close = h('button', { class: 'btn close-share', type: 'button' }, 'close');
    close.addEventListener('click', () => this.toggle(false));
    // the position is named, not gestured at: "where I am now" is unverifiable from a dialog that
    // covers the transport, and it keeps counting while this is open (sync(), every SYNC_MS)
    const at = h('span', { class: 'share-at' }, fmtStart(startSeconds(this.links.withTime)));
    this.at = at;
    const opt = h('label', { class: 'share-opt' }, time, ' start playback at ', at, ' (adds ', h('code', null, 't='), ' at the end)');
    this.opt = opt;
    this.box.append(
      h('h2', { id: 'share-title' }, 'share'),
      // everything but the close button scrolls, so the way out stays on screen on short viewports
      h('div', { class: 'share-scroll' }, h('div', { class: 'share-row' }, field, copy), opt, note, h('dl', { class: 'share-params' })),
      h('div', { class: 'btnrow share-foot' }, close),
    );
    this.renderParams();
  }

  /** one row per parameter, the ones this link actually carries marked `.on` */
  private renderParams(): void {
    const list = this.box.querySelector('.share-params');
    if (!list) return;
    clear(list);
    const q = this.url.indexOf('?');
    const present = new Set<UrlParam>(paramsIn(q < 0 ? '' : this.url.slice(q)));
    for (const key of URL_PARAM_ORDER) {
      list.append(h('dt', { class: present.has(key) ? 'on' : undefined }, h('code', null, `${key}=`)), h('dd', { class: present.has(key) ? 'on' : undefined }, URL_PARAM_HELP[key]));
    }
  }
}
