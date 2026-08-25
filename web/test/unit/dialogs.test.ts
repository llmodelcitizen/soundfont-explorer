/**
 * @vitest-environment happy-dom
 *
 * The three overlay dialogs, in a DOM: the parts of #30/#33/#34 that are behaviour rather than
 * paint — where focus goes, what the window keymap is allowed to see through an open dialog, and
 * what a copy actually copies. (Pure geometry lives in scripts/geometry.mjs.)
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { KeymapOverlay } from '../../src/ui/keymapOverlay';
import { ShareDialog, copyText } from '../../src/ui/share';
import { SettingsModal } from '../../src/ui/settings';
import { installKeyboard, type KeyActions } from '../../src/input/keyboard';
import { DEFAULT_PREFS } from '../../src/state/prefs';
import type { ShareLinks } from '../../src/state/urlstate';

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
function press(target: Element | Window, key: string): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
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

  it('hands focus back when its own close button hides it', () => {
    const onClose = vi.fn();
    const overlay = new KeymapOverlay({ onClose });
    document.body.append(overlay.el);
    overlay.toggle(true);
    (overlay.el.querySelector('.close-keymap') as HTMLButtonElement).click();

    expect(overlay.visible).toBe(false);
    expect(onClose).toHaveBeenCalledTimes(1);
    // closing something already closed must not steal focus from wherever it is
    overlay.toggle(false);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('ShareDialog (#30)', () => {
  const links = (t: number, theme = 'modern'): ShareLinks => ({
    withTime: t > 0 ? `https://x.test/?theme=${theme}&t=${t}` : `https://x.test/?theme=${theme}`,
    withoutTime: `https://x.test/?theme=${theme}`,
  });

  it('opens with the whole link selected', () => {
    const dialog = new ShareDialog({ links: () => links(12) });
    document.body.append(dialog.el);
    dialog.toggle(true);
    const field = dialog.el.querySelector('.share-url') as HTMLInputElement;
    expect(field.value).toBe('https://x.test/?theme=modern&t=12');
    expect(document.activeElement).toBe(field);
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, field.value.length]);
  });

  it('copies the link for where the app is now, not where it was when the dialog opened', async () => {
    let state = links(12);
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const dialog = new ShareDialog({ links: () => state });
    document.body.append(dialog.el);
    dialog.toggle(true);

    state = links(99, 'amiga'); // the song/theme/position moved on behind the open dialog
    const button = dialog.el.querySelector('.share-copy') as HTMLButtonElement;
    button.focus(); // where a real click leaves the focus: on the button, not the field
    button.click();
    await vi.waitFor(() => expect(dialog.el.querySelector('.share-note')?.textContent).toBe('copied'));

    expect(writeText).toHaveBeenCalledWith('https://x.test/?theme=amiga&t=99');
    const field = dialog.el.querySelector('.share-url') as HTMLInputElement;
    expect(field.value).toBe('https://x.test/?theme=amiga&t=99');
    // the async-clipboard path must put the selection back: the dialog claims to be modal
    expect(document.activeElement).toBe(field);
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, field.value.length]);
  });

  it('keeps the window keymap out while it is open, and closes on Escape', () => {
    const dialog = new ShareDialog({ links: () => links(12) });
    document.body.append(dialog.el);
    const a = actions();
    const uninstall = installKeyboard(window as unknown as Window, a);
    dialog.toggle(true);
    const field = dialog.el.querySelector('.share-url') as HTMLInputElement;
    // the copy button is where the focus lands once someone uses the dialog, and a BUTTON is not
    // "typing": without the dialog swallowing them these keys reach the app behind it
    const button = dialog.el.querySelector('.share-copy') as HTMLButtonElement;

    for (const key of [' ', 't', ']', 'd']) press(button, key);
    expect(a.toggle).not.toHaveBeenCalled();
    expect(a.theme).not.toHaveBeenCalled();
    expect(a.song).not.toHaveBeenCalled();
    expect(a.debug).not.toHaveBeenCalled();

    press(field, 'Escape');
    expect(dialog.visible).toBe(false);
    expect(a.escape).not.toHaveBeenCalled();
    uninstall();
  });

  it('hands focus back however it is closed', () => {
    const onClose = vi.fn();
    const dialog = new ShareDialog({ links: () => links(12), onClose });
    document.body.append(dialog.el);

    dialog.toggle(true);
    (dialog.el.querySelector('.close-share') as HTMLButtonElement).click();
    expect(onClose).toHaveBeenCalledTimes(1);

    dialog.toggle(true);
    press(dialog.el.querySelector('.share-url') as HTMLInputElement, 'Escape');
    expect(onClose).toHaveBeenCalledTimes(2);

    dialog.toggle(false); // already closed: nothing to hand back
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('disables the timestamp option when both links are the same string', () => {
    let state = links(0);
    const dialog = new ShareDialog({ links: () => state });
    document.body.append(dialog.el);
    dialog.toggle(true);
    const box = dialog.el.querySelector('.share-time') as HTMLInputElement;
    expect(state.withTime).toBe(state.withoutTime);
    expect(box.disabled).toBe(true);
    expect(dialog.el.querySelector('.share-opt')?.classList.contains('off')).toBe(true);

    dialog.toggle(false);
    state = links(30);
    dialog.toggle(true);
    expect((dialog.el.querySelector('.share-time') as HTMLInputElement).disabled).toBe(false);
    expect(dialog.el.querySelector('.share-opt')?.classList.contains('off')).toBe(false);
  });

  it('marks exactly the parameters the shown link carries', () => {
    const dialog = new ShareDialog({ links: () => links(12) });
    document.body.append(dialog.el);
    dialog.toggle(true);
    const on = () => Array.from(dialog.el.querySelectorAll('.share-params dt.on code')).map((c) => c.textContent);
    expect(on()).toEqual(['theme=', 't=']);

    const box = dialog.el.querySelector('.share-time') as HTMLInputElement;
    box.checked = false;
    box.dispatchEvent(new Event('change'));
    expect((dialog.el.querySelector('.share-url') as HTMLInputElement).value).toBe('https://x.test/?theme=modern');
    expect(on()).toEqual(['theme=']);
  });

  it('falls back to the selected field when the async clipboard is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    const exec = vi.fn(() => true);
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true });
    const field = document.createElement('input');
    field.value = 'https://x.test/?song=a';
    document.body.append(field);

    expect(await copyText(field, field.value)).toBe(true);
    expect(exec).toHaveBeenCalledWith('copy');
    expect(document.activeElement).toBe(field);
  });
});

describe('SettingsModal full screen (#34)', () => {
  const callbacks = () => ({ onChange: vi.fn(), onResetTrack: vi.fn(), onResetAll: vi.fn(), onResetFont: vi.fn(), trackTitle: () => 'a track' });

  it('repaints only the full-screen control, keeping focus and a half-typed value', () => {
    const settings = new SettingsModal({ ...DEFAULT_PREFS }, callbacks());
    document.body.append(settings.el);
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
    const settings = new SettingsModal({ ...DEFAULT_PREFS }, callbacks());
    document.body.append(settings.el);
    settings.reportFullscreen('nope, this browser cannot');

    expect(settings.visible).toBe(true);
    const hint = settings.el.querySelector('.settings .colfoot span.warn');
    expect(hint?.textContent).toBe('nope, this browser cannot');
    // and it survives the repaint a fullscreenchange would trigger
    settings.refresh();
    expect(settings.el.querySelector('.settings .colfoot span.warn')?.textContent).toBe('nope, this browser cannot');
    settings.refresh('');
    expect(settings.el.querySelector('.settings .colfoot span.warn')).toBeNull();
  });
});
