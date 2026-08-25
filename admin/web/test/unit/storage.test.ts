// safeStorage, the admin copy (web/src/state/storage.ts is the other one; the web suite pins
// that the two files stay identical). The behaviour that matters here: the library view's
// folder state and autoplay flag must never take the SPA down when the store is blocked.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { safeStorage } from '../../src/storage';

afterEach(() => vi.unstubAllGlobals());

describe('safeStorage', () => {
  it('round-trips strings and JSON', () => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => values.get(k) ?? null,
      setItem: (k: string, v: string) => values.set(k, v),
      removeItem: (k: string) => values.delete(k),
    });
    safeStorage.set('a', '1');
    expect(safeStorage.get('a')).toBe('1');
    safeStorage.setJson('open', ['x', 'y']);
    expect(safeStorage.getJson<string[]>('open')).toEqual(['x', 'y']);
    safeStorage.remove('a');
    expect(safeStorage.get('a')).toBeNull();
    values.set('bad', '{oops');
    expect(safeStorage.getJson('bad')).toBeNull();
  });

  it('reads null and drops writes when the store throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('quota'); },
      removeItem: () => { throw new Error('blocked'); },
    });
    expect(safeStorage.get('a')).toBeNull();
    expect(safeStorage.getJson('a')).toBeNull();
    expect(() => safeStorage.set('a', '1')).not.toThrow();
    expect(() => safeStorage.setJson('a', [1])).not.toThrow();
    expect(() => safeStorage.remove('a')).not.toThrow();
  });
});
