import { describe, expect, it } from 'vitest';
import { ByteLRU } from '../../src/audio/cache/lru';

describe('ByteLRU', () => {
  it('evicts least-recently-used but never pinned entries', () => {
    const evicted: string[] = [];
    const lru = new ByteLRU<number>(10, (v) => v, (k) => evicted.push(k));
    lru.set('a', 4);
    lru.set('b', 4);
    lru.pin('a');
    lru.get('a');
    lru.set('c', 4); // over budget: b is LRU+unpinned → evicted
    expect(evicted).toEqual(['b']);
    expect(lru.has('a')).toBe(true);
    lru.set('d', 4); // a pinned → c goes
    expect(evicted).toEqual(['b', 'c']);
    expect(lru.bytes).toBe(8);
    lru.unpin('a');
    lru.set('e', 4);
    expect(evicted).toEqual(['b', 'c', 'a']);
  });

  it('counts hits and misses', () => {
    const lru = new ByteLRU<number>(100, (v) => v);
    lru.set('x', 1);
    lru.get('x');
    lru.get('y');
    expect(lru.hits).toBe(1);
    expect(lru.misses).toBe(1);
  });

  it('never evicts the entry just inserted, even when over budget', () => {
    const lru = new ByteLRU<number>(5, (v) => v);
    lru.set('big', 50);
    expect(lru.has('big')).toBe(true); // the newest survives (consumer needs it once)
    expect(lru.overcommitted).toBe(true);
    lru.set('a', 1);
    expect(lru.has('big')).toBe(false); // evicted on the next insert
    expect(lru.has('a')).toBe(true);
    lru.pin('a');
    lru.set('big', 50);
    expect(lru.has('big')).toBe(true);
    expect(lru.has('a')).toBe(true);
  });
});
