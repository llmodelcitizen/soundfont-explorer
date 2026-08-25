/**
 * safeStorage: the one place that knows localStorage can throw. Six modules used to carry
 * their own try/catch around it (13 of them), so "a blocked store reads as a miss and a
 * failed write is dropped" is pinned here, once.
 */
import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { safeStorage } from '../../src/state/storage';

afterEach(() => vi.unstubAllGlobals());

/** a store that throws on every operation: private mode, blocked storage, a full quota */
const throwing = {
  getItem: vi.fn(() => { throw new DOMException('denied', 'SecurityError'); }),
  setItem: vi.fn(() => { throw new DOMException('quota', 'QuotaExceededError'); }),
  removeItem: vi.fn(() => { throw new DOMException('denied', 'SecurityError'); }),
};

const working = () => {
  const values = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => values.get(k) ?? null,
    setItem: (k: string, v: string) => values.set(k, v),
    removeItem: (k: string) => values.delete(k),
  });
  return values;
};

describe('safeStorage with a working store', () => {
  it('round-trips strings and JSON and removes', () => {
    const values = working();
    safeStorage.set('a', 'x');
    expect(safeStorage.get('a')).toBe('x');
    safeStorage.setJson('b', { n: 1, list: ['a'] });
    expect(values.get('b')).toBe('{"n":1,"list":["a"]}');
    expect(safeStorage.getJson<{ n: number }>('b')).toEqual({ n: 1, list: ['a'] });
    safeStorage.remove('a');
    expect(safeStorage.get('a')).toBeNull();
  });

  it('reads a missing key and malformed JSON as null', () => {
    const values = working();
    expect(safeStorage.get('nope')).toBeNull();
    expect(safeStorage.getJson('nope')).toBeNull();
    values.set('bad', '{oops');
    expect(safeStorage.getJson('bad')).toBeNull();
    values.set('empty', '');
    expect(safeStorage.getJson('empty')).toBeNull();
  });
});

describe('safeStorage when the store throws', () => {
  it('reads null and drops writes without throwing', () => {
    vi.stubGlobal('localStorage', throwing);
    expect(safeStorage.get('k')).toBeNull();
    expect(safeStorage.getJson('k')).toBeNull();
    expect(() => safeStorage.set('k', 'v')).not.toThrow();
    expect(() => safeStorage.setJson('k', { a: 1 })).not.toThrow();
    expect(() => safeStorage.remove('k')).not.toThrow();
    expect(throwing.setItem).toHaveBeenCalled();
  });

  it('survives having no localStorage at all', () => {
    // this suite runs in node: `localStorage` is not merely blocked, it is undeclared, so
    // every call would be a ReferenceError without the guard
    expect(safeStorage.get('k')).toBeNull();
    expect(safeStorage.getJson('k')).toBeNull();
    expect(() => safeStorage.set('k', 'v')).not.toThrow();
    expect(() => safeStorage.setJson('k', 1)).not.toThrow();
    expect(() => safeStorage.remove('k')).not.toThrow();
  });

  it('drops a value JSON cannot serialise instead of throwing at the caller', () => {
    working();
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(() => safeStorage.setJson('c', circular)).not.toThrow();
    expect(safeStorage.get('c')).toBeNull();
  });
});

describe('the admin copy', () => {
  it('is byte-for-byte the same module', () => {
    // web/ and admin/web are separate npm projects with include: ["src"] and separate
    // deploys, so this helper is copied rather than imported across; this is the pin that
    // stops the two copies from drifting.
    const here = readFileSync(new URL('../../src/state/storage.ts', import.meta.url), 'utf8');
    const admin = readFileSync(new URL('../../../admin/web/src/storage.ts', import.meta.url), 'utf8');
    expect(admin).toBe(here);
  });
});
