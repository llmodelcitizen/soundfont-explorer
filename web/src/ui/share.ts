/**
 * Share dialog (the ⤴ button, immediately left of ⚙): the page URL, selected and ready to copy,
 * offered with and without the `t=` start time, plus what every URL parameter means. No social
 * network anything — a link and a copy button (issue #30).
 */
import { URL_PARAM_HELP, URL_PARAM_ORDER, paramsIn, type ShareLinks, type UrlParam } from '../state/urlstate';
import { clear, h } from './dom';
import { activeElement, restoreFocus, trapTab } from './focus';

export interface ShareCallbacks {
  /** the links for the state as it stands right now — re-read before every copy */
  links(): ShareLinks;
  /** where focus goes when the dialog closes and the control that opened it is gone */
  onClose?(): void;
}

/** why the timestamp option has nothing to do; shown as text, not only as a tooltip (#30) */
const SAME_LINKS = 'the track is at the start, so both links are the same';

/** copy through the async clipboard, falling back to the selected field (older Safari, no HTTPS) */
export async function copyText(field: HTMLInputElement, text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* denied or unavailable: fall through to the selection copy */
  }
  try {
    field.focus({ preventScroll: true });
    field.select();
    return document.execCommand?.('copy') ?? false;
  } catch {
    return false;
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
  private field: HTMLInputElement | null = null;
  private time: HTMLInputElement | null = null;
  private opt: HTMLElement | null = null;
  private why: HTMLElement | null = null;
  private note: HTMLElement | null = null;
  /** what the field is showing, so a re-sync that changes nothing touches nothing */
  private shownUrl: string | null = null;
  private shownSame = false;
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
    this.selectField();
    this.startSyncing();
  }

  /** re-read the links: the app's state moves on (position, theme, song) while the dialog is open */
  private sync(): void {
    this.links = this.cb.links();
    // at the top of the track the two links are identical: an option that does nothing reads as broken
    const same = this.links.withTime === this.links.withoutTime;
    const url = this.url;
    if (url === this.shownUrl && same === this.shownSame) return;
    const moved = this.shownUrl !== null && url !== this.shownUrl;
    this.shownUrl = url;
    this.shownSame = same;
    const field = this.field;
    if (field) {
      field.value = url;
      // the dialog opens with the field selected for ⌘/Ctrl + C: keep the selection on the new text
      if (field.ownerDocument.activeElement === field) field.select();
    }
    const time = this.time;
    if (time) {
      time.disabled = same;
      // the box says what the link in the field carries, never a sticky preference of its own
      time.checked = this.includeTime;
    }
    this.opt?.classList.toggle('off', same);
    if (this.opt) this.opt.title = same ? SAME_LINKS : '';
    if (this.why) this.why.textContent = same ? SAME_LINKS : '';
    // 'copied' would be a lie about the link now in the field
    if (moved && this.note) this.note.textContent = '';
    this.renderParams();
  }

  private startSyncing(): void {
    if (this.timer === null) this.timer = setInterval(() => this.sync(), ShareDialog.SYNC_MS);
  }

  private stopSyncing(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }

  private selectField(): void {
    const field = this.field;
    if (!field) return;
    field.focus({ preventScroll: true });
    field.select();
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
    // size=1 keeps the field from claiming its 20-character intrinsic width: at 16px Fixedsys
    // (win95, coarse pointer) that alone made the box wider than a 320px phone (#30)
    const field = h('input', { type: 'text', class: 'share-url', size: '1', readonly: true, spellcheck: 'false', 'aria-label': 'link to this page' }) as HTMLInputElement;
    field.value = this.url;
    field.addEventListener('focus', () => field.select());
    this.field = field;
    const note = h('span', { class: 'muted small share-note', role: 'status' }, '');
    this.note = note;
    const copy = h('button', { class: 'btn primary share-copy', type: 'button', title: 'copy the link' }, 'copy');
    copy.addEventListener('click', () => {
      this.sync(); // copy the link for where the app is now, not for where it was when this opened
      void copyText(field, this.url).then((ok) => {
        note.textContent = ok ? 'copied' : 'press ⌘/Ctrl + C to copy';
        // the async-clipboard path never touches the field: put the selection back on it, so the
        // dialog keeps the focus its aria-modal promises and ⌘C still works
        this.selectField();
      });
    });
    const time = h('input', { type: 'checkbox', class: 'share-time' }) as HTMLInputElement;
    time.checked = this.includeTime;
    this.time = time;
    time.addEventListener('change', () => {
      this.withTime = time.checked;
      note.textContent = '';
      this.sync();
      this.selectField();
    });
    const close = h('button', { class: 'btn close-share', type: 'button' }, 'close');
    close.addEventListener('click', () => this.toggle(false));
    const opt = h('label', { class: 'share-opt' }, time, ' start where I am now (adds ', h('code', null, 't='), ' at the end)');
    this.opt = opt;
    // a title is invisible on a touch screen: the reason the option is greyed out is text (#30)
    const why = h('span', { class: 'muted small share-why' });
    this.why = why;
    this.box.append(
      h('h2', { id: 'share-title' }, 'share'),
      // everything but the close button scrolls, so the way out stays on screen on short viewports
      h('div', { class: 'share-scroll' }, h('div', { class: 'share-row' }, field, copy), opt, why, note, h('div', { class: 'share-heading' }, 'what the link says'), h('dl', { class: 'share-params' })),
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
