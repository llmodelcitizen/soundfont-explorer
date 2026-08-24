import { describe, expect, it, vi } from 'vitest';
import { installKeyboard, KEYMAP, type KeyActions } from '../../src/input/keyboard';

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
    theme: vi.fn(),
    debug: vi.fn(),
    keymap: vi.fn(),
    settings: vi.fn(),
  };
}

function key(target: EventTarget, value: string, repeat = false): void {
  const event = new Event('keydown', { cancelable: true });
  Object.defineProperties(event, {
    key: { value },
    repeat: { value: repeat },
    metaKey: { value: false },
    ctrlKey: { value: false },
    altKey: { value: false },
    shiftKey: { value: false },
  });
  target.dispatchEvent(event);
}

describe('keyboard shortcuts', () => {
  it('maps X to stop and V to favorite without repeating either action', () => {
    const target = new EventTarget();
    const a = actions();
    const uninstall = installKeyboard(target as unknown as Window, a);

    key(target, 'x');
    key(target, 'V');
    key(target, 'x', true);
    key(target, 'v', true);

    expect(a.stop).toHaveBeenCalledTimes(1);
    expect(a.favorite).toHaveBeenCalledTimes(1);
    uninstall();
  });

  it('shows every added shortcut in the key guide', () => {
    expect(KEYMAP).toContainEqual(['X', 'stop and rewind']);
    expect(KEYMAP).toContainEqual(['V', 'favorite / unfavorite current variant']);
  });
});
