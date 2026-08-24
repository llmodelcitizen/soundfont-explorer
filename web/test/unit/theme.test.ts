import { afterEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_THEME, nextTheme, readTheme } from '../../src/ui/theme';

afterEach(() => vi.unstubAllGlobals());

describe('themes', () => {
  it.each([
    ['modern', 'win95'],
    ['win95', 'amiga'],
    ['amiga', 'modern'],
  ] as const)('cycles %s to %s', (current, next) => {
    expect(nextTheme(current)).toBe(next);
  });

  it('accepts Amiga from the URL or persisted preference', () => {
    expect(readTheme('amiga')).toBe('amiga');
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'amiga') });
    expect(readTheme()).toBe('amiga');
  });

  it('keeps Windows 95 URL and persisted values compatible', () => {
    expect(readTheme('win95')).toBe('win95');
    vi.stubGlobal('localStorage', { getItem: vi.fn(() => 'win95') });
    expect(readTheme()).toBe('win95');
  });

  it.each(['dark', 'system', 'AMIGA', 'unknown'])('falls back for invalid legacy value %s', (value) => {
    expect(readTheme(value)).toBe(DEFAULT_THEME);
  });
});
