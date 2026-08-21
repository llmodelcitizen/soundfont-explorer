import { describe, expect, it } from 'vitest';
import { DEFAULT_PREFS, ListenedLedger, loadPrefs } from '../../src/state/prefs';

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

describe('favorites', () => {
  it('toggles, persists in memory without localStorage, notifies', async () => {
    const { Favorites } = await import('../../src/state/prefs');
    const f = new Favorites();
    const seen: number[] = [];
    f.onChange((ids) => seen.push(ids.size));
    expect(f.toggle('a')).toBe(true);
    expect(f.toggle('b')).toBe(true);
    expect(f.toggle('a')).toBe(false);
    expect([...f.all()]).toEqual(['b']);
    expect(seen).toEqual([1, 2, 1]);
  });
});
