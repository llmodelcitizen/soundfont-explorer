/**
 * @vitest-environment happy-dom
 *
 * The three overlay dialogs, in a DOM: the parts of #30/#33/#34 that are behaviour rather than
 * paint — where focus goes, what the window keymap is allowed to see through an open dialog, and
 * what a copy actually copies. (Pure geometry lives in scripts/geometry.mjs.)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KeymapOverlay } from '../../src/ui/keymapOverlay';
import { COPY_FAILED_KEYS, COPY_FAILED_TOUCH, ShareDialog, coarsePointer, copyText, fmtStart, startSeconds, type ShareCallbacks } from '../../src/ui/share';
import { SettingsModal } from '../../src/ui/settings';
import { FULLSCREEN_REFUSED, FULLSCREEN_UNSUPPORTED } from '../../src/ui/fullscreen';
import { focusables } from '../../src/ui/focus';
import { VariantList } from '../../src/ui/list';
import { parseCatalog } from '../../src/contracts/catalog';
import { installKeyboard, type KeyActions } from '../../src/input/keyboard';
import { DEFAULT_PREFS } from '../../src/state/prefs';
import type { ShareLinks } from '../../src/state/urlstate';
import { makeSet } from './fakes';

function actions(): KeyActions {
  return {
    step: vi.fn(),
    stepEnd: vi.fn(),
    page: vi.fn(),
    home: vi.fn(),
    end: vi.fn(),
    toggle: vi.fn(),
    stop: vi.fn(),
    skip: vi.fn(),
    loop: vi.fn(),
    mute: vi.fn(),
    favorite: vi.fn(),
    focusSearch: vi.fn(),
    escape: vi.fn(),
    song: vi.fn(),
    pinA: vi.fn(),
    ab: vi.fn(),
    filters: vi.fn(),
    fullscreen: vi.fn(),
    theme: vi.fn(),
    debug: vi.fn(),
    keymap: vi.fn(),
    settings: vi.fn(),
  };
}

/** a keydown that bubbles the way a real one does, so a dialog can stop it reaching the window */
function press(target: Element | Window, key: string, init: KeyboardEventInit = {}): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

/** a control outside the dialogs, standing in for the header button that opens one */
function opener(): HTMLButtonElement {
  const button = document.createElement('button');
  document.body.append(button);
  button.focus();
  return button;
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('KeymapOverlay (#33)', () => {
  it('keeps the close button out of the scrolling list, so it cannot scroll away', () => {
    const overlay = new KeymapOverlay();
    document.body.append(overlay.el);
    const box = overlay.el.querySelector('.overlay-box');
    const foot = overlay.el.querySelector('.keymap-foot');
    const scroll = overlay.el.querySelector('.keymap-scroll');
    expect(box?.classList.contains('keys')).toBe(true);
    expect(scroll?.querySelector('table.keymap')).toBeTruthy();
    // the button is a sibling of the scroller, not a row inside it
    expect(foot?.parentElement).toBe(box);
    expect(scroll?.contains(overlay.el.querySelector('.close-keymap'))).toBe(false);
    // and no prose telling people to press Esc instead
    expect(overlay.el.textContent).not.toMatch(/esc to close/i);
  });

  it('does not let Space on the focused close button reach the window play/pause', () => {
    const overlay = new KeymapOverlay();
    document.body.append(overlay.el);
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);
    overlay.toggle(true);
    const button = overlay.el.querySelector('.close-keymap') as HTMLButtonElement;
    expect(document.activeElement).toBe(button);

    press(button, ' ');
    expect(a.toggle).not.toHaveBeenCalled(); // Space is the button's own activation, not play/pause

    // every other key still belongs to the window keymap
    press(button, 't');
    expect(a.theme).toHaveBeenCalledTimes(1);
    uninstall();
  });

  it('does not let Tab swap the pinned variant behind the open screen', () => {
    const overlay = new KeymapOverlay();
    document.body.append(overlay.el);
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);
    overlay.toggle(true);
    const button = overlay.el.querySelector('.close-keymap') as HTMLButtonElement;

    // the close button is the only thing in the box: Tab has nowhere to go but must not fall out
    for (const shiftKey of [false, true]) {
      const event = press(button, 'Tab', { shiftKey });
      expect(event.defaultPrevented).toBe(true);
      expect(document.activeElement).toBe(button);
    }
    expect(a.ab).not.toHaveBeenCalled();
    uninstall();
  });

  it('hands focus back when its own close button hides it', () => {
    const onClose = vi.fn();
    const overlay = new KeymapOverlay({ onClose });
    document.body.append(overlay.el);
    overlay.toggle(true);
    (overlay.el.querySelector('.close-keymap') as HTMLButtonElement).click();

    expect(overlay.visible).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1); // nothing focusable opened it: the app focuses the list
    // closing something already closed must not steal focus from wherever it is
    overlay.toggle(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('gives focus back to the control that opened it, not to <body>', () => {
    const onClose = vi.fn();
    const button = opener();
    const overlay = new KeymapOverlay({ onClose });
    document.body.append(overlay.el);

    overlay.toggle(true);
    expect(document.activeElement).toBe(overlay.el.querySelector('.close-keymap'));
    (overlay.el.querySelector('.close-keymap') as HTMLButtonElement).click();

    expect(document.activeElement).toBe(button);
    expect(onClose).not.toHaveBeenCalled(); // the fallback is only for a dialog nothing focusable opened
  });
});

describe('ShareDialog (#30)', () => {
  const links = (t: number, theme = 'modern'): ShareLinks => ({
    withTime: t > 0 ? `https://x.test/?theme=${theme}&t=${t}` : `https://x.test/?theme=${theme}`,
    withoutTime: `https://x.test/?theme=${theme}`,
  });
  const open: ShareDialog[] = [];
  /** every dialog re-reads its links on a timer while it is open: close them all after each test */
  const share = (read: () => ShareLinks, extra: Partial<ShareCallbacks> = {}): ShareDialog => {
    const dialog = new ShareDialog({ links: read, ...extra });
    document.body.append(dialog.el);
    open.push(dialog);
    return dialog;
  };
  /** a pointing device / a finger, the only thing that decides whether a copy highlights the link */
  const desktop = { coarsePointer: () => false };
  const phone = { coarsePointer: () => true };
  const field = (dialog: ShareDialog) => dialog.el.querySelector('.share-url') as HTMLTextAreaElement;
  const timeBox = (dialog: ShareDialog) => dialog.el.querySelector('.share-time') as HTMLInputElement;
  const copyBtn = (dialog: ShareDialog) => dialog.el.querySelector('.share-copy') as HTMLButtonElement;
  const note = (dialog: ShareDialog) => dialog.el.querySelector('.share-note') as HTMLElement;
  const opt = (dialog: ShareDialog) => dialog.el.querySelector('.share-opt') as HTMLElement;
  const selected = (dialog: ShareDialog) => field(dialog).value.slice(field(dialog).selectionStart ?? 0, field(dialog).selectionEnd ?? 0);
  /** the async clipboard, present or absent — both paths are real on the platforms this ships to */
  const withClipboard = (writeText: ((text: string) => Promise<void>) | null) => {
    Object.defineProperty(navigator, 'clipboard', { value: writeText ? { writeText } : undefined, configurable: true });
  };

  afterEach(() => {
    for (const dialog of open.splice(0)) dialog.toggle(false);
    Reflect.deleteProperty(navigator, 'clipboard');
    Reflect.deleteProperty(document, 'execCommand');
  });

  it('opens with the whole link shown but nothing highlighted, and focus on the copy button', () => {
    const dialog = share(() => links(12), desktop);
    dialog.toggle(true);
    expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=12');
    // a wall of highlight is not a greeting, and on a phone it is two drag handles and a callout bar
    expect(selected(dialog)).toBe('');
    // an aria-modal dialog still has to hold focus: the primary action takes it
    expect(document.activeElement).toBe(copyBtn(dialog));
  });

  it('shows the link in a box deep enough to read it whole, not a one-line field to drag through', () => {
    const dialog = share(() => links(12));
    dialog.toggle(true);
    expect(field(dialog).tagName).toBe('TEXTAREA');
    expect(Number(field(dialog).getAttribute('rows'))).toBeGreaterThanOrEqual(3);
    // cols=1 is the size=1 trick: without it the 20-character intrinsic width alone makes the box
    // wider than a 320px viewport at 16px Fixedsys (win95, coarse pointer)
    expect(field(dialog).getAttribute('cols')).toBe('1');
    // soft wrapping keeps the value one line of text: a copied link must not carry newlines
    expect(field(dialog).getAttribute('wrap')).toBe('soft');
    expect(field(dialog).value).not.toMatch(/\n/);
  });

  it('copies the link for where the app is now, not where it was when the dialog opened', async () => {
    let state = links(12);
    const writeText = vi.fn(async () => {});
    withClipboard(writeText);
    const dialog = share(() => state, desktop);
    dialog.toggle(true);

    state = links(99, 'amiga'); // the song/theme/position moved on behind the open dialog
    copyBtn(dialog).click();
    await vi.waitFor(() => expect(note(dialog).textContent).toBe('copied'));

    expect(writeText).toHaveBeenCalledWith('https://x.test/?theme=amiga&t=99');
    expect(field(dialog).value).toBe('https://x.test/?theme=amiga&t=99');
  });

  it('highlights the copied link on a desktop, where a selection is a mouse-width affordance', async () => {
    withClipboard(vi.fn(async () => {}));
    const dialog = share(() => links(12), desktop);
    dialog.toggle(true);
    copyBtn(dialog).click();
    await vi.waitFor(() => expect(note(dialog).textContent).toBe('copied'));

    // the async-clipboard path never touches the field: the dialog puts the selection on itself,
    // which shows what was taken and leaves the ⌘/Ctrl + C it invites copying the same string
    expect(document.activeElement).toBe(field(dialog));
    expect(selected(dialog)).toBe('https://x.test/?theme=modern&t=12');
  });

  it('never highlights on a coarse pointer, where the handles are bigger than the link', async () => {
    withClipboard(vi.fn(async () => {}));
    const dialog = share(() => links(12), phone);
    dialog.toggle(true);
    copyBtn(dialog).click();
    await vi.waitFor(() => expect(note(dialog).textContent).toBe('copied'));

    expect(selected(dialog)).toBe('');
    expect(document.activeElement).toBe(copyBtn(dialog)); // focus stays where the finger was
  });

  it('takes back even the selection its fallback needed, on a coarse pointer', async () => {
    withClipboard(null); // an http:// page on a phone: no async clipboard at all
    Object.defineProperty(document, 'execCommand', { value: vi.fn(() => true), configurable: true });
    const dialog = share(() => links(12), phone);
    dialog.toggle(true);
    copyBtn(dialog).click();
    await vi.waitFor(() => expect(note(dialog).textContent).toBe('copied'));

    // the selection copy has to select something; what it must not do is leave it there
    expect(selected(dialog)).toBe('');
    expect(document.activeElement).toBe(copyBtn(dialog));
  });

  it('says so when neither clipboard path worked, in words the platform can act on', async () => {
    withClipboard(vi.fn(async () => Promise.reject(new Error('denied'))));
    Object.defineProperty(document, 'execCommand', { value: vi.fn(() => false), configurable: true });

    const onDesktop = share(() => links(12), desktop);
    onDesktop.toggle(true);
    copyBtn(onDesktop).click();
    await vi.waitFor(() => expect(note(onDesktop).textContent).toBe(COPY_FAILED_KEYS));
    // a failure that reads like the success it is not is the same as no message at all
    expect(note(onDesktop).classList.contains('warn')).toBe(true);

    const onPhone = share(() => links(12), phone);
    onPhone.toggle(true);
    copyBtn(onPhone).click();
    await vi.waitFor(() => expect(note(onPhone).textContent).toBe(COPY_FAILED_TOUCH));
    // there is no ⌘ and no Ctrl on the device this is telling
    expect(COPY_FAILED_TOUCH).not.toMatch(/⌘|Ctrl/);
  });

  it('keeps the shown link in step with the app, so ⌘C and the copy button agree', () => {
    vi.useFakeTimers();
    try {
      withClipboard(vi.fn(async () => {}));
      let state = links(10);
      const dialog = share(() => state, desktop);
      dialog.toggle(true);
      expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=10');
      copyBtn(dialog).click(); // the highlight goes on synchronously, before any clipboard promise

      state = links(40); // 30 s of playback later, with the dialog still open
      vi.advanceTimersByTime(ShareDialog.SYNC_MS + 10);
      expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=40');
      // the highlight moves onto the new text: what is selected is still what the button would copy
      expect(selected(dialog)).toBe('https://x.test/?theme=modern&t=40');

      dialog.toggle(false);
      state = links(80);
      vi.advanceTimersByTime(10 * ShareDialog.SYNC_MS);
      expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=40'); // closed: nothing to keep up with
    } finally {
      vi.useRealTimers();
    }
  });

  it('names the start time the link carries, and keeps counting while the dialog is open', () => {
    vi.useFakeTimers();
    try {
      let state = links(75);
      const dialog = share(() => state);
      dialog.toggle(true);
      // "where I am now" is unverifiable from a dialog that covers the transport: say the time
      expect(opt(dialog).textContent).toContain('start playback at');
      expect(opt(dialog).textContent).not.toContain('where I am now');
      expect(dialog.el.querySelector('.share-at')?.textContent).toBe('1:15');

      state = links(3672); // an hour and change later
      vi.advanceTimersByTime(ShareDialog.SYNC_MS + 10);
      expect(dialog.el.querySelector('.share-at')?.textContent).toBe('1:01:12');

      // and with the option off the label still counts: it is what ticking the box would add,
      // and the shown link (withoutTime) never changes to trigger a repaint on its own
      timeBox(dialog).checked = false;
      timeBox(dialog).dispatchEvent(new Event('change'));
      expect(field(dialog).value).toBe('https://x.test/?theme=modern');
      state = links(3700);
      vi.advanceTimersByTime(ShareDialog.SYNC_MS + 10);
      expect(dialog.el.querySelector('.share-at')?.textContent).toBe('1:01:40');
      expect(field(dialog).value).toBe('https://x.test/?theme=modern');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the window keymap out while it is open, and closes on Escape', () => {
    const dialog = share(() => links(12));
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);
    dialog.toggle(true);
    // the copy button is where the focus lands, and a BUTTON is not "typing": without the dialog
    // swallowing them these keys reach the app behind it
    const button = copyBtn(dialog);

    for (const key of [' ', 't', ']', 'd']) press(button, key);
    expect(a.toggle).not.toHaveBeenCalled();
    expect(a.theme).not.toHaveBeenCalled();
    expect(a.song).not.toHaveBeenCalled();
    expect(a.debug).not.toHaveBeenCalled();

    press(field(dialog), 'Escape');
    expect(dialog.visible).toBe(false);
    expect(a.escape).not.toHaveBeenCalled();
    uninstall();
  });

  it('wraps Tab inside the box instead of letting focus walk out onto <body>', () => {
    const dialog = share(() => links(12));
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);
    dialog.toggle(true);
    const box = dialog.el.querySelector('.overlay-box.share') as HTMLElement;
    const close = box.querySelector('.close-share') as HTMLButtonElement;
    expect(focusables(box)).toEqual([field(dialog), copyBtn(dialog), timeBox(dialog), close]);

    // forward off the last control comes back to the first
    close.focus();
    const forward = press(close, 'Tab');
    expect(forward.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(field(dialog));

    // and backwards off the first goes to the last
    const back = press(field(dialog), 'Tab', { shiftKey: true });
    expect(back.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(close);

    // in the middle the browser's own Tab still moves focus — but never the window's A/B swap
    field(dialog).focus();
    const middle = press(field(dialog), 'Tab');
    expect(middle.defaultPrevented).toBe(false);
    expect(a.ab).not.toHaveBeenCalled();
    uninstall();
  });

  it('hands focus back however it is closed', () => {
    const onClose = vi.fn();
    const dialog = share(() => links(12), { onClose });

    dialog.toggle(true);
    (dialog.el.querySelector('.close-share') as HTMLButtonElement).click();
    expect(onClose).toHaveBeenCalledTimes(1);

    dialog.toggle(true);
    press(field(dialog), 'Escape');
    expect(onClose).toHaveBeenCalledTimes(2);

    dialog.toggle(false); // already closed: nothing to hand back
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('gives focus back to the control that opened it, not to <body>', () => {
    const onClose = vi.fn();
    const button = opener();
    const dialog = share(() => links(12), { onClose });

    dialog.toggle(true);
    (dialog.el.querySelector('.close-share') as HTMLButtonElement).click();
    expect(document.activeElement).toBe(button);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('disables the timestamp option when both links are the same string, and explains nothing', () => {
    let state = links(0);
    const dialog = share(() => state);
    dialog.toggle(true);
    expect(state.withTime).toBe(state.withoutTime);
    expect(timeBox(dialog).disabled).toBe(true);
    // an unusable option must not claim it is adding a t= the link does not carry
    expect(timeBox(dialog).checked).toBe(false);
    expect(opt(dialog).classList.contains('off')).toBe(true);
    // "0:00" is the whole explanation: the sentence that used to spell it out is gone, and so is
    // the tooltip that said the same thing to a pointer only
    expect(dialog.el.querySelector('.share-at')?.textContent).toBe('0:00');
    expect(dialog.el.textContent).not.toMatch(/at the start/i);
    expect(dialog.el.querySelector('.share-why')).toBeNull();
    expect(opt(dialog).getAttribute('title')).toBeNull();

    dialog.toggle(false);
    state = links(30);
    dialog.toggle(true);
    expect(timeBox(dialog).disabled).toBe(false);
    expect(timeBox(dialog).checked).toBe(true);
    expect(opt(dialog).classList.contains('off')).toBe(false);
  });

  it('lists what every parameter means with no heading shouting over it', () => {
    const dialog = share(() => links(12));
    dialog.toggle(true);
    // the rows carry their own meaning; the label above them was a caption on a seven-row table
    expect(dialog.el.querySelector('.share-heading')).toBeNull();
    expect(dialog.el.textContent).not.toMatch(/what the link says/i);
    expect(dialog.el.querySelectorAll('.share-params dt').length).toBe(7);
  });

  it('starts every share from the whole link, however the last one ended', () => {
    const dialog = share(() => links(30));
    dialog.toggle(true);
    timeBox(dialog).checked = false;
    timeBox(dialog).dispatchEvent(new Event('change'));
    expect(field(dialog).value).toBe('https://x.test/?theme=modern');

    dialog.toggle(false);
    dialog.toggle(true);
    expect(timeBox(dialog).checked).toBe(true);
    expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=30');
  });

  it('marks exactly the parameters the shown link carries', () => {
    const dialog = share(() => links(12));
    dialog.toggle(true);
    const on = () => Array.from(dialog.el.querySelectorAll('.share-params dt.on code')).map((c) => c.textContent);
    expect(on()).toEqual(['theme=', 't=']);

    timeBox(dialog).checked = false;
    timeBox(dialog).dispatchEvent(new Event('change'));
    expect(field(dialog).value).toBe('https://x.test/?theme=modern');
    expect(on()).toEqual(['theme=']);
  });

  it('falls back to a selection copy when there is no async clipboard, without a tick in between', () => {
    withClipboard(null); // what an http:// page — or an older Safari — actually offers
    const exec = vi.fn(() => true);
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true });
    const input = document.createElement('input');
    input.readOnly = true;
    input.value = 'https://x.test/?song=a';
    document.body.append(input);

    let settled: boolean | null = null;
    void copyText(input, input.value).then((ok) => {
      settled = ok;
    });
    // the copy has to happen inside the gesture that is still running: iOS and Android both spend
    // the user activation on the first await, and execCommand a microtask later is refused
    expect(exec).toHaveBeenCalledWith('copy');
    expect(document.activeElement).toBe(input);
    // iOS Safari will not select a readonly field and only extends a selection into a
    // content-editable form control — both come off for the copy, and both go straight back
    expect(input.readOnly).toBe(true);
    expect(input.hasAttribute('contenteditable')).toBe(false);
    return vi.waitFor(() => expect(settled).toBe(true));
  });

  it('falls back the same way when the async clipboard is there and refuses', async () => {
    withClipboard(vi.fn(async () => Promise.reject(new Error('NotAllowedError'))));
    const exec = vi.fn(() => true);
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true });
    const input = document.createElement('input');
    input.value = 'https://x.test/?song=a';
    document.body.append(input);

    expect(await copyText(input, input.value)).toBe(true);
    expect(exec).toHaveBeenCalledWith('copy');
  });

  it('reports the failure rather than swallowing it when both paths fail', async () => {
    withClipboard(vi.fn(async () => Promise.reject(new Error('denied'))));
    Object.defineProperty(document, 'execCommand', { value: vi.fn(() => false), configurable: true });
    const input = document.createElement('input');
    input.value = 'https://x.test/?song=a';
    document.body.append(input);

    expect(await copyText(input, input.value)).toBe(false);
  });

  it('puts the text it was asked to copy into the field the fallback copies from', async () => {
    withClipboard(null);
    Object.defineProperty(document, 'execCommand', { value: vi.fn(() => true), configurable: true });
    const input = document.createElement('input');
    input.value = 'stale';
    document.body.append(input);

    expect(await copyText(input, 'https://x.test/?song=a&t=9')).toBe(true);
    // execCommand copies the selection, which is the field — so the field has to hold the link
    expect(input.value).toBe('https://x.test/?song=a&t=9');
  });

  it('asks the pointer, not the user agent, whether a selection is worth drawing', () => {
    const asked: string[] = [];
    const view = (matches: boolean) =>
      ({
        matchMedia: (query: string) => {
          asked.push(query);
          return { matches } as MediaQueryList;
        },
      }) as unknown as Window;

    expect(coarsePointer(view(true))).toBe(true);
    expect(coarsePointer(view(false))).toBe(false);
    // `pointer` is the primary input: a laptop with a touchscreen is still a laptop
    expect(new Set(asked)).toEqual(new Set(['(pointer: coarse)']));
    expect(coarsePointer(null)).toBe(false); // nothing to ask: the side that highlights
  });

  it('reads the start time back out of the link, and says it the way a clock does', () => {
    expect(startSeconds('https://x.test/?theme=modern&t=75')).toBe(75);
    expect(startSeconds('https://x.test/?theme=modern')).toBe(0); // from the top: no t= at all
    expect(startSeconds('https://x.test/')).toBe(0);
    expect(startSeconds('https://x.test/?t=nonsense')).toBe(0);

    expect(fmtStart(0)).toBe('0:00');
    expect(fmtStart(9)).toBe('0:09');
    expect(fmtStart(75)).toBe('1:15');
    expect(fmtStart(600)).toBe('10:00');
    expect(fmtStart(3672)).toBe('1:01:12');
    expect(fmtStart(-5)).toBe('0:00');
    expect(fmtStart(Number.NaN)).toBe('0:00');
  });
});

describe('SettingsModal full screen (#34)', () => {
  const callbacks = (onClose = vi.fn()) => ({ onChange: vi.fn(), onResetTrack: vi.fn(), onResetAll: vi.fn(), onResetFont: vi.fn(), onClose });
  const settingsModal = (onClose = vi.fn()) => {
    const settings = new SettingsModal({ ...DEFAULT_PREFS }, callbacks(onClose));
    document.body.append(settings.el);
    return settings;
  };
  const warning = (settings: SettingsModal) => settings.el.querySelector('.settings .colfoot span.warn');

  it('repaints only the full-screen control, keeping focus and a half-typed value', () => {
    const settings = settingsModal();
    settings.toggle(true);
    const num = settings.el.querySelector('.settings .num') as HTMLInputElement;
    const button = settings.el.querySelector('.fullscreen-btn') as HTMLButtonElement;
    num.focus();
    num.value = '7.5'; // typed, not yet committed: `change` has not fired

    settings.refresh(); // what a fullscreenchange does — from Esc, F11, or another surface

    expect(settings.el.querySelector('.settings .num')).toBe(num); // not rebuilt
    expect(settings.el.querySelector('.fullscreen-btn')).toBe(button);
    expect(document.activeElement).toBe(num);
    expect(num.value).toBe('7.5');
  });

  it('says why full screen did not happen, next to the button that offers it', () => {
    const settings = settingsModal();
    settings.reportFullscreen('nope, this browser cannot');

    expect(settings.visible).toBe(true);
    expect(warning(settings)?.textContent).toBe('nope, this browser cannot');
    // and it survives the repaint a fullscreenchange would trigger
    settings.refresh();
    expect(warning(settings)?.textContent).toBe('nope, this browser cannot');
    settings.refresh('');
    expect(warning(settings)).toBeNull();
  });

  it('reports into an already-open dialog without rebuilding it', () => {
    const settings = settingsModal();
    settings.toggle(true);
    const num = settings.el.querySelector('.settings .num') as HTMLInputElement;
    num.focus();
    num.value = '7.5';

    settings.reportFullscreen('nope, that was refused'); // Shift + F with Settings already open

    expect(settings.el.querySelector('.settings .num')).toBe(num); // the rebuild refresh() avoids
    expect(num.value).toBe('7.5');
    expect(document.activeElement).toBe(num);
    expect(warning(settings)?.textContent).toBe('nope, that was refused');
  });

  it('forgets a refusal when the dialog closes: it was about one attempt', () => {
    const settings = settingsModal();
    settings.reportFullscreen('nope, that was refused');
    expect(warning(settings)?.textContent).toBe('nope, that was refused');

    settings.toggle(false);
    settings.toggle(true);
    expect(warning(settings)).toBeNull();
    const hint = settings.el.querySelector('.settings .colfoot .fullscreen-btn + span');
    expect(hint?.classList.contains('warn')).toBe(false);
    expect(hint?.textContent).not.toBe('nope, that was refused');
  });

  it('tells a refused request apart from a browser with no full screen at all', async () => {
    const root = document.documentElement;
    Object.defineProperty(document, 'fullscreenEnabled', { value: true, configurable: true });
    Object.defineProperty(root, 'requestFullscreen', { value: () => Promise.reject(new Error('denied')), configurable: true });
    try {
      const settings = settingsModal();
      settings.toggle(true);
      const button = settings.el.querySelector('.fullscreen-btn') as HTMLButtonElement;
      expect(button.disabled).toBe(false); // this browser does have full screen

      button.click();
      await vi.waitFor(() => expect(warning(settings)?.textContent).toBe(FULLSCREEN_REFUSED));
      // the iPhone 'add to Home Screen' advice would be a lie here
      expect(FULLSCREEN_REFUSED).not.toBe(FULLSCREEN_UNSUPPORTED);
      expect(FULLSCREEN_REFUSED).not.toMatch(/Home Screen/);
    } finally {
      Reflect.deleteProperty(document, 'fullscreenEnabled');
      Reflect.deleteProperty(root, 'requestFullscreen');
    }
  });

  it('gives focus back when it closes, to the opener or to the app', () => {
    const button = opener();
    const settings = settingsModal();
    settings.toggle(true);
    (settings.el.querySelector('.close-settings') as HTMLButtonElement).click();
    expect(document.activeElement).toBe(button);

    // opened from the keyboard, with nothing focusable behind it: the app takes focus back
    const onClose = vi.fn();
    const keyboardOpened = settingsModal(onClose);
    document.body.focus();
    (document.activeElement as HTMLElement | null)?.blur?.();
    keyboardOpened.toggle(true);
    keyboardOpened.toggle(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('scrolls its settings, never its close button, and keeps Tab inside itself', () => {
    const settings = settingsModal();
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);
    settings.toggle(true);
    const box = settings.el.querySelector('.overlay-box.settings') as HTMLElement;
    const scroll = settings.el.querySelector('.settings-scroll') as HTMLElement;
    const foot = settings.el.querySelector('.settings-foot') as HTMLElement;
    const close = settings.el.querySelector('.close-settings') as HTMLButtonElement;

    expect(scroll.parentElement).toBe(box);
    expect(foot.parentElement).toBe(box);
    // every section scrolls, including the Tracks options moved in from the caption (#28)
    const sections = Array.from(scroll.querySelectorAll('section.setting'));
    expect(sections.length).toBe(4);
    expect(sections.map((s) => s.querySelector('.setting-title')?.textContent ?? 'listened'))
      .toEqual(['listened', 'Tracks', 'Display', 'List columns']);
    expect(scroll.contains(close)).toBe(false); // the way out cannot scroll off a short viewport

    const items = focusables(box);
    const first = items[0] as HTMLElement;
    const last = items[items.length - 1] as HTMLElement;
    last.focus();
    const forward = press(last, 'Tab');
    expect(forward.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);
    expect(a.ab).not.toHaveBeenCalled();
    uninstall();
  });
});

describe('SettingsModal "reset for this track"', () => {
  const open = (onResetTrack: () => void = vi.fn()) => {
    const settings = new SettingsModal({ ...DEFAULT_PREFS }, { onChange: vi.fn(), onResetTrack, onResetAll: vi.fn(), onResetFont: vi.fn() });
    document.body.append(settings.el);
    settings.toggle(true);
    return settings;
  };
  const resetButton = (settings: SettingsModal) =>
    Array.from(settings.el.querySelectorAll('.settings .btn')).find((b) => b.textContent?.trim() === 'reset for this track') as HTMLButtonElement;

  it('resets the track that is current when it is clicked, not the one that was current when the dialog opened', () => {
    // stands in for the app's `this.song`: the track the button really means, read at click time
    let current = 'the track we opened on';
    const reset: string[] = [];
    const settings = open(() => void reset.push(current));

    current = 'the track that is on now';
    resetButton(settings).click();

    expect(reset).toEqual(['the track that is on now']);
  });

  it('cannot assume the track stood still: [ and ] step tracks through an open dialog', () => {
    const settings = open();
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);

    // the dialog stops only Escape, so the window keymap still steps tracks underneath it — which
    // is why the dialog must not remember a track of its own
    press(settings.el.querySelector('.close-settings')!, ']');

    expect(a.song).toHaveBeenCalledWith(1);
    uninstall();
  });

  it('names no track, so there is nothing in the dialog that can go stale', () => {
    const settings = open();

    expect(settings.el.textContent).not.toMatch(/current track/i);
    // the button still says what it acts on; its tooltip says when it decides which track that is
    expect(resetButton(settings).title).toMatch(/now/);
  });
});

describe('the list as a focus target (#30/#33)', () => {
  it('can take focus back from a closing dialog without offering itself to Tab', () => {
    const catalog = parseCatalog({
      schema: 1,
      engines: [],
      facets: {},
      variants: [{ id: 'a', label: 'Alpha', engine: 'adlmidi', chip: 'opl2', type: 'fm', facets: {} }],
    });
    const { set } = makeSet(['a'], 8);
    const list = new VariantList(catalog, set, { onClick: vi.fn(), onStickyClick: vi.fn(), onSort: vi.fn() }, { isFavorite: () => false, listenedSeconds: () => 0, listened: () => false });
    document.body.append(list.el);

    // the app's focusList() is only worth calling if the element it focuses can hold focus
    expect(list.el.getAttribute('tabindex')).toBe('-1');
    expect(focusables(document.body)).not.toContain(list.el);
    list.el.focus();
    expect(document.activeElement).toBe(list.el);
  });
});
