import { describe, expect, it } from 'vitest';
import { DEFAULT_PREFS, Favorites, ListenedLedger, TrackPositions, applyPreservePreference, bootPosition, loadPrefs, sanitizeLedger, urlPosition } from '../../src/state/prefs';

describe('prefs + listened ledger', () => {
  it('defaults without localStorage', () => {
    expect(loadPrefs()).toEqual(DEFAULT_PREFS);
  });

  it('accumulates seconds per song × variant and reports listened sets by threshold', () => {
    let t = 0;
    const l = new ListenedLedger(() => t);
    expect(l.add('s1', 'a', 1.2)).toBeCloseTo(1.2);
    l.add('s1', 'a', 2.0);
    l.add('s1', 'b', 0.5);
    l.add('s2', 'a', 9);
    expect(l.seconds('s1', 'a')).toBeCloseTo(3.2);
    expect([...l.listened('s1', 3)]).toEqual(['a']);
    expect([...l.listened('s1', 0.25)].sort()).toEqual(['a', 'b']);
    expect([...l.listened('s2', 3)]).toEqual(['a']);
    expect(l.add('s1', 'a', -5)).toBeCloseTo(3.2); // negative/NaN ignored
    l.resetSong('s1');
    expect(l.listened('s1', 0).size).toBe(0);
    expect(l.seconds('s2', 'a')).toBe(9);
    l.resetAll();
    expect(l.seconds('s2', 'a')).toBe(0);
  });
});

describe('prefs: stored shape', () => {
  /** run fn with a localStorage stub holding `items` (node has no localStorage at all) */
  function withPrefs(raw: string, fn: () => void): void {
    const g = globalThis as { localStorage?: unknown };
    g.localStorage = { getItem: (k: string) => (k === 'sfp.prefs.v1' ? raw : null), setItem: () => undefined, removeItem: () => undefined };
    try {
      fn();
    } finally {
      delete g.localStorage;
    }
  }

  it('steps to the next track by default', () => {
    expect(DEFAULT_PREFS.autoNextTrack).toBe(true);
  });

  it('gives a pre-existing prefs object the automatic-stepping default', () => {
    // exactly what a browser that last ran the previous build has stored
    withPrefs(JSON.stringify({ listenedAfterS: 4, preserveTrackPosition: false, columns: ['chip'], mobileColumns: ['chip'] }), () => {
      const p = loadPrefs();
      expect(p.autoNextTrack).toBe(true);
      expect(p.listenedAfterS).toBe(4);
      expect(p.preserveTrackPosition).toBe(false);
      expect(p.columns).toEqual(['chip']);
    });
  });

  it('keeps a stored choice and ignores a non-boolean one', () => {
    withPrefs(JSON.stringify({ autoNextTrack: false }), () => expect(loadPrefs().autoNextTrack).toBe(false));
    withPrefs(JSON.stringify({ autoNextTrack: 'no' }), () => expect(loadPrefs().autoNextTrack).toBe(true));
  });
});

describe('per-track positions', () => {
  it('starts unseen tracks at zero and recalls each visited track independently', () => {
    const positions = new TrackPositions();
    expect(positions.recall('unplayed')).toBe(0);
    positions.remember('one', 12.5);
    positions.remember('two', 47);
    expect(positions.recall('one')).toBe(12.5);
    expect(positions.recall('two')).toBe(47);
    expect(positions.recall('unplayed')).toBe(0);
  });

  it('clears remembered positions when preservation is disabled', () => {
    const positions = new TrackPositions();
    positions.remember('one', 12.5);
    positions.clear();
    expect(positions.recall('one')).toBe(0);
  });
});

describe('changing "preserve track position"', () => {
  /** a TrackPositions with two visited tracks, plus a spy for the URL's saved position */
  const scenario = () => {
    const positions = new TrackPositions();
    positions.remember('one', 12.5);
    positions.remember('two', 47);
    let syncs = 0;
    return { positions, sync: () => { syncs += 1; }, synced: () => syncs };
  };

  it('zeroes every remembered position, not just future ones, and drops the URL position', () => {
    const s = scenario();
    applyPreservePreference(true, false, s.positions, s.sync);
    expect(s.positions.recall('one')).toBe(0);
    expect(s.positions.recall('two')).toBe(0);
    expect(s.synced()).toBe(1);
  });

  // the symmetric case: nothing to clear, but the URL must carry a position again or a reload
  // straight after re-enabling the option would start the audible track from zero
  it('puts the URL position back when the option goes on again', () => {
    const s = scenario();
    applyPreservePreference(false, true, s.positions, s.sync);
    expect(s.synced()).toBe(1);
    expect(s.positions.recall('one')).toBe(12.5);
  });

  it('does nothing at all when the option did not change', () => {
    for (const both of [true, false]) {
      const s = scenario();
      applyPreservePreference(both, both, s.positions, s.sync);
      expect(s.positions.recall('one')).toBe(12.5);
      expect(s.synced()).toBe(0);
    }
  });
});

describe('which position survives a reload', () => {
  it('restores a shared or bookmarked t= only while positions are preserved', () => {
    expect(bootPosition(true, 90)).toBe(90);
    expect(bootPosition(true, undefined)).toBe(0);
    expect(bootPosition(false, 90)).toBe(0);
  });

  it('writes no position for a track that ran to its end, or when positions are not preserved', () => {
    expect(urlPosition(true, false, 188)).toBe(188);
    expect(urlPosition(true, true, 188)).toBeUndefined();
    expect(urlPosition(false, false, 188)).toBeUndefined();
  });
});

describe('favorites', () => {
  it('toggles and persists in memory without localStorage', () => {
    const f = new Favorites();
    expect(f.toggle('a')).toBe(true);
    expect(f.toggle('b')).toBe(true);
    expect(f.has('b')).toBe(true);
    expect(f.toggle('a')).toBe(false);
    expect(f.has('a')).toBe(false);
    expect([...f.all()]).toEqual(['b']);
  });
});

describe('listened ledger: stored shape', () => {
  /** run fn with a localStorage stub holding `items` (node has no localStorage at all) */
  function withStorage(items: Record<string, string>, fn: () => void): void {
    const store = new Map(Object.entries(items));
    const g = globalThis as { localStorage?: unknown };
    g.localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v), removeItem: (k: string) => store.delete(k) };
    try {
      fn();
    } finally {
      delete g.localStorage;
    }
  }

  it('drops entries that are not {song: {variant: seconds}} instead of throwing from add()', () => {
    const bad = { s1: 5, s2: { a: 'x', b: -1, c: 3, d: Number.NaN }, s3: null, s4: [1, 2], s5: 'str' };
    withStorage({ 'sfp.listened.v1': JSON.stringify(bad) }, () => {
      const l = new ListenedLedger(() => 0);
      expect(() => l.add('s1', 'a', 1)).not.toThrow(); // s1 was a number: `s1.a = 1` threw before
      expect(l.seconds('s1', 'a')).toBe(1);
      expect(l.seconds('s2', 'c')).toBe(3);
      expect(l.seconds('s2', 'a')).toBe(0);
      expect(l.seconds('s2', 'b')).toBe(0);
      expect([...l.listened('s2', 1)]).toEqual(['c']);
    });
    for (const raw of ['[]', '"str"', '5', 'null', '{bad json']) {
      withStorage({ 'sfp.listened.v1': raw }, () => {
        const l = new ListenedLedger(() => 0);
        expect(l.add('s', 'v', 2)).toBe(2);
      });
    }
    expect(sanitizeLedger({ s: { v: 1.5 } })).toEqual({ s: { v: 1.5 } });
    expect(sanitizeLedger(undefined)).toEqual({});
  });

  it('keeps a song literally called __proto__ as an own key instead of setting a prototype', () => {
    // JSON.parse makes '__proto__' an own property; assigning it onto a plain object would not
    const raw: unknown = JSON.parse('{"__proto__": {"a": 4}, "s1": {"b": 2}}');
    const clean = sanitizeLedger(raw);
    expect(Object.prototype.hasOwnProperty.call(clean, '__proto__')).toBe(true);
    expect(Object.getPrototypeOf({})).toBe(Object.prototype); // nothing global was touched
    // (an object *literal* cannot express this: `{ __proto__: x }` sets the prototype)
    const round = JSON.parse(JSON.stringify(clean)) as Record<string, unknown>;
    expect(Object.entries(round)).toEqual([['__proto__', { a: 4 }], ['s1', { b: 2 }]]);
    withStorage({ 'sfp.listened.v1': JSON.stringify(JSON.parse('{"__proto__": {"a": 4}}')) }, () => {
      const l = new ListenedLedger(() => 0);
      expect(l.seconds('__proto__', 'a')).toBe(4); // survives the round trip, not silently dropped
      expect(l.add('__proto__', 'a', 1)).toBe(5);
      expect([...l.listened('__proto__', 1)]).toEqual(['a']);
    });
  });
});
