/**
 * @vitest-environment happy-dom
 *
 * The three overlay dialogs, in a DOM: the parts of #30/#33/#34 that are behaviour rather than
 * paint — where focus goes, what the window keymap is allowed to see through an open dialog, and
 * what a copy actually copies. (Pure geometry lives in scripts/geometry.mjs.)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KeymapOverlay } from '../../src/ui/keymapOverlay';
import { ShareDialog, copyText } from '../../src/ui/share';
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
  const share = (read: () => ShareLinks, onClose?: () => void): ShareDialog => {
    const dialog = new ShareDialog({ links: read, onClose });
    document.body.append(dialog.el);
    open.push(dialog);
    return dialog;
  };
  const field = (dialog: ShareDialog) => dialog.el.querySelector('.share-url') as HTMLInputElement;
  const timeBox = (dialog: ShareDialog) => dialog.el.querySelector('.share-time') as HTMLInputElement;

  afterEach(() => {
    for (const dialog of open.splice(0)) dialog.toggle(false);
  });

  it('opens with the whole link selected, in a field that cannot claim a phone-wide column', () => {
    const dialog = share(() => links(12));
    dialog.toggle(true);
    expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=12');
    expect(document.activeElement).toBe(field(dialog));
    expect([field(dialog).selectionStart, field(dialog).selectionEnd]).toEqual([0, field(dialog).value.length]);
    // without size=1 the input keeps its 20-character intrinsic width, which at 16px Fixedsys
    // (win95, coarse pointer) makes the whole box wider than a 320px viewport
    expect(field(dialog).getAttribute('size')).toBe('1');
  });

  it('copies the link for where the app is now, not where it was when the dialog opened', async () => {
    let state = links(12);
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const dialog = share(() => state);
    dialog.toggle(true);

    state = links(99, 'amiga'); // the song/theme/position moved on behind the open dialog
    const button = dialog.el.querySelector('.share-copy') as HTMLButtonElement;
    button.focus(); // where a real click leaves the focus: on the button, not the field
    button.click();
    await vi.waitFor(() => expect(dialog.el.querySelector('.share-note')?.textContent).toBe('copied'));

    expect(writeText).toHaveBeenCalledWith('https://x.test/?theme=amiga&t=99');
    expect(field(dialog).value).toBe('https://x.test/?theme=amiga&t=99');
    // the async-clipboard path must put the selection back: the dialog claims to be modal
    expect(document.activeElement).toBe(field(dialog));
    expect([field(dialog).selectionStart, field(dialog).selectionEnd]).toEqual([0, field(dialog).value.length]);
  });

  it('keeps the shown link in step with the app, so ⌘C and the copy button agree', () => {
    vi.useFakeTimers();
    try {
      let state = links(10);
      const dialog = share(() => state);
      dialog.toggle(true);
      expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=10');

      state = links(40); // 30 s of playback later, with the dialog still open
      vi.advanceTimersByTime(ShareDialog.SYNC_MS + 10);
      expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=40');
      // still selected, so the ⌘/Ctrl + C the dialog invites copies what it is showing
      expect(document.activeElement).toBe(field(dialog));
      expect([field(dialog).selectionStart, field(dialog).selectionEnd]).toEqual([0, field(dialog).value.length]);

      dialog.toggle(false);
      state = links(80);
      vi.advanceTimersByTime(10 * ShareDialog.SYNC_MS);
      expect(field(dialog).value).toBe('https://x.test/?theme=modern&t=40'); // closed: nothing to keep up with
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the window keymap out while it is open, and closes on Escape', () => {
    const dialog = share(() => links(12));
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);
    dialog.toggle(true);
    // the copy button is where the focus lands once someone uses the dialog, and a BUTTON is not
    // "typing": without the dialog swallowing them these keys reach the app behind it
    const button = dialog.el.querySelector('.share-copy') as HTMLButtonElement;

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
    expect(focusables(box)).toEqual([field(dialog), box.querySelector('.share-copy'), timeBox(dialog), close]);

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
    const dialog = share(() => links(12), onClose);

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
    const dialog = share(() => links(12), onClose);

    dialog.toggle(true);
    (dialog.el.querySelector('.close-share') as HTMLButtonElement).click();
    expect(document.activeElement).toBe(button);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('disables the timestamp option when both links are the same string, and says why in text', () => {
    let state = links(0);
    const dialog = share(() => state);
    dialog.toggle(true);
    expect(state.withTime).toBe(state.withoutTime);
    expect(timeBox(dialog).disabled).toBe(true);
    // an unusable option must not claim it is adding a t= the link does not carry
    expect(timeBox(dialog).checked).toBe(false);
    expect(dialog.el.querySelector('.share-opt')?.classList.contains('off')).toBe(true);
    // a title is invisible on a phone: the reason is on screen
    expect(dialog.el.querySelector('.share-why')?.textContent).toMatch(/at the start/);

    dialog.toggle(false);
    state = links(30);
    dialog.toggle(true);
    expect(timeBox(dialog).disabled).toBe(false);
    expect(timeBox(dialog).checked).toBe(true);
    expect(dialog.el.querySelector('.share-opt')?.classList.contains('off')).toBe(false);
    expect(dialog.el.querySelector('.share-why')?.textContent).toBe('');
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

  it('falls back to the selected field when the async clipboard is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    const exec = vi.fn(() => true);
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true });
    const input = document.createElement('input');
    input.value = 'https://x.test/?song=a';
    document.body.append(input);

    expect(await copyText(input, input.value)).toBe(true);
    expect(exec).toHaveBeenCalledWith('copy');
    expect(document.activeElement).toBe(input);
  });
});

describe('SettingsModal full screen (#34)', () => {
  const callbacks = (onClose = vi.fn()) => ({ onChange: vi.fn(), onResetTrack: vi.fn(), onResetAll: vi.fn(), onResetFont: vi.fn(), trackTitle: () => 'a track', onClose });
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
