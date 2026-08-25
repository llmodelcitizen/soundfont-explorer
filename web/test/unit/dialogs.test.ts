/**
 * @vitest-environment happy-dom
 *
 * The overlay dialogs, in a DOM: the parts of #30/#33/#34 that are behaviour rather than paint —
 * where focus goes, and what the window keymap is allowed to see through an open dialog. (Pure
 * geometry lives in scripts/geometry.mjs, which needs a live server and does not run in CI.)
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { KeymapOverlay } from '../../src/ui/keymapOverlay';
import { SettingsModal } from '../../src/ui/settings';
import { installKeyboard, type KeyActions } from '../../src/input/keyboard';
import { DEFAULT_PREFS } from '../../src/state/prefs';

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
