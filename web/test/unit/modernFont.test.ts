import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearModernFontPreference, DEFAULT_MODERN_FONT, MODERN_FONTS, MODERN_FONT_KEY, nextModernFont, readModernFont, saveModernFont } from '../../src/ui/modernFont';

afterEach(() => vi.unstubAllGlobals());

describe('modern font preference', () => {
  it('offers a distinct 10–20 face cycle and wraps to the default', () => {
    expect(MODERN_FONTS.length).toBeGreaterThanOrEqual(10);
    expect(MODERN_FONTS.length).toBeLessThanOrEqual(20);
    expect(new Set(MODERN_FONTS.map((font) => font.id)).size).toBe(MODERN_FONTS.length);
    expect(nextModernFont(MODERN_FONTS.at(-1)!.id)).toBe(DEFAULT_MODERN_FONT);
  });

  it('persists a valid selection and rejects stale values', () => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: vi.fn((key: string) => values.get(key) ?? null),
      setItem: vi.fn((key: string, value: string) => values.set(key, value)),
      removeItem: vi.fn((key: string) => values.delete(key)),
    });
    saveModernFont('space-grotesk');
    expect(values.get(MODERN_FONT_KEY)).toBe('space-grotesk');
    expect(readModernFont()).toBe('space-grotesk');
    values.set(MODERN_FONT_KEY, 'removed-font');
    expect(readModernFont()).toBe(DEFAULT_MODERN_FONT);
    values.set(MODERN_FONT_KEY, 'orbitron');
    expect(clearModernFontPreference()).toBe(DEFAULT_MODERN_FONT);
    expect(values.has(MODERN_FONT_KEY)).toBe(false);
  });
});
