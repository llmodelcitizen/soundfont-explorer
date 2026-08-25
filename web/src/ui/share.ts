/**
 * Share dialog (the ⤴ button, immediately left of ⚙): the page URL, selected and ready to copy,
 * offered with and without the `t=` start time, plus what every URL parameter means. No social
 * network anything — a link and a copy button (issue #30).
 */
import { URL_PARAM_HELP, URL_PARAM_ORDER, paramsIn, type ShareLinks, type UrlParam } from '../state/urlstate';
import { clear, h } from './dom';

export interface ShareCallbacks {
  /** the links for the state as it stands the moment the dialog opens */
  links(): ShareLinks;
}

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
  readonly el: HTMLElement;
  visible = false;
  private box: HTMLElement;
  private links: ShareLinks = { withTime: '', withoutTime: '' };
  /** the ⏱ checkbox: include the current position */
  private withTime = true;
  private field: HTMLInputElement | null = null;

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
      }
    });
    this.render();
  }

  toggle(force?: boolean): void {
    this.visible = force ?? !this.visible;
    this.el.classList.toggle('hidden', !this.visible);
    if (!this.visible) return;
    this.links = this.cb.links();
    this.render();
    // the URL arrives highlighted, so ⌘C works without touching the Copy button
    const field = this.field;
    if (field) {
      field.focus({ preventScroll: true });
      field.select();
    }
  }

  private get url(): string {
    return this.withTime ? this.links.withTime : this.links.withoutTime;
  }

  private render(): void {
    clear(this.box);
    const field = h('input', { type: 'text', class: 'share-url', readonly: true, spellcheck: 'false', 'aria-label': 'link to this page' }) as HTMLInputElement;
    field.value = this.url;
    field.addEventListener('focus', () => field.select());
    this.field = field;
    const note = h('span', { class: 'muted small share-note', role: 'status' }, '');
    const copy = h('button', { class: 'btn primary share-copy', type: 'button', title: 'copy the link' }, 'copy');
    copy.addEventListener('click', () => {
      void copyText(field, this.url).then((ok) => {
        note.textContent = ok ? 'copied' : 'press ⌘/Ctrl + C to copy';
      });
    });
    const time = h('input', { type: 'checkbox', class: 'share-time' }) as HTMLInputElement;
    time.checked = this.withTime;
    time.addEventListener('change', () => {
      this.withTime = time.checked;
      field.value = this.url;
      note.textContent = '';
      this.renderParams();
      field.focus({ preventScroll: true });
      field.select();
    });
    const close = h('button', { class: 'btn close-share', type: 'button' }, 'close');
    close.addEventListener('click', () => this.toggle(false));
    this.box.append(
      h('h2', { id: 'share-title' }, 'share'),
      h('div', { class: 'share-row' }, field, copy),
      h('label', { class: 'share-opt' }, time, ' start where I am now (adds ', h('code', null, 't='), ' at the end)'),
      note,
      h('div', { class: 'share-heading' }, 'what the link says'),
      h('dl', { class: 'share-params' }),
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
