import { describe, expect, it } from 'vitest';
import { Timeline } from '../../src/audio/clock';
import { Fetcher } from '../../src/audio/net/fetcher';
import { Chain } from '../../src/audio/scheduler';
import { SegmentStore } from '../../src/audio/store';
import { keyStr } from '../../src/audio/types';
import { FakeAudioContext, FakeBuffer, FakeDecoder, FakeFetch, makeSet } from './fakes';

/** a chain over variant `a` whose scrub slices are all decoded (listen slices are not) */
function chainFor(D = 8) {
  const { set, objects } = makeSet(['a'], D);
  const ctx = new FakeAudioContext();
  const store = new SegmentStore(set, new Fetcher(8, new FakeFetch(objects).fn, () => 0), new FakeDecoder(), { decodedBytes: 64 << 20, compressedBytes: 8 << 20 }, () => 0);
  for (let i = 0; i < set.slices; i++) store.decoded.set(keyStr({ v: 'a', tier: 's', i }), new FakeBuffer(2.14, 48000, 2, `a/s/${i}`));
  const timeline = new Timeline(() => ctx.currentTime, D);
  timeline.play(0);
  const chain = new Chain(ctx, 'a', set, store, timeline, ctx.createGain(), 'l');
  return { set, ctx, store, timeline, chain };
}

/** a set neither tier's slices cover: nothing can advance fill()'s tail past u = 4 */
function malformed(set: ReturnType<typeof chainFor>['set']): void {
  set.slices = 2;
  set.listen = { ...set.listen, slice_s: 2, slices: 2 };
}

describe('Chain.fill', () => {
  it('stops instead of spinning when the set\'s slices do not cover its duration', () => {
    const h = chainFor(8);
    malformed(h.set); // 2 × 2 s slices for an 8 s song → every u ≥ 4 maps to the last slice, which ends at 4
    let peeks = 0;
    const peek = h.store.peek.bind(h.store);
    h.store.peek = (k) => {
      if (++peeks > 1000) throw new Error('fill() did not terminate');
      return peek(k);
    };
    h.ctx.currentTime = 4;
    expect(h.chain.fill(4)).toBeNull(); // target = 4 + LOOKAHEAD: [0, 4) schedules, then no segment can advance past 4
    expect(h.chain.tail).toBe(4);
    expect(peeks).toBeLessThan(50);
    // every later tick is bounded too, and nothing accumulates for the uncovered stretch
    const scheduled = h.chain.scheduled.length;
    expect(h.chain.fill(4)).toBeNull();
    expect(h.chain.scheduled.length).toBe(scheduled);
  });

  it('reports the uncovered stretch instead of stalling silently', () => {
    // breaking out of fill() is indistinguishable from "fully scheduled" for the engine: audio
    // simply runs out at the tail, so the stall must reach the debug panel (store.lastError)
    const h = chainFor(8);
    malformed(h.set);
    h.ctx.currentTime = 4;
    expect(h.store.lastError).toBeNull();
    h.chain.fill(4);
    expect(h.chain.coverageStalls).toBe(1);
    expect(h.store.stats.coverageStalls).toBe(1); // where the debug panel reads it
    expect(h.store.lastError).toMatch(/a\/s\/1: segment ends at 4 ≤ tail 4 .* do not cover its duration/);
    h.chain.fill(4);
    expect(h.chain.coverageStalls).toBe(2);
  });

  it('reports the same uncovered stretch once, not on every tick', () => {
    // fill() runs on every engine tick and every frame: rewriting lastError each time would
    // bury every other fetch/decode error the debug panel could show for the whole song
    const h = chainFor(8);
    malformed(h.set);
    h.ctx.currentTime = 4;
    h.chain.fill(4);
    expect(h.store.lastError).toMatch(/do not cover its duration/);
    h.store.lastError = 'a/s/0: boom'; // a real error arrives while the malformed set plays
    for (let i = 0; i < 5; i++) h.chain.fill(4);
    expect(h.chain.coverageStalls).toBe(6); // still counted every time
    expect(h.store.lastError).toBe('a/s/0: boom');
  });

  it('falls back to the tier that covers the stretch instead of stalling on the preferred one', () => {
    // only the listen tier runs short here; the scrub tier covers the whole duration, so fill()
    // must keep going on it rather than stopping on whichever tier pick() happened to prefer
    const h = chainFor(8);
    h.set.listen = { ...h.set.listen, slice_s: 2, slices: 1 }; // listen covers [0, 2) only
    h.store.decoded.set(keyStr({ v: 'a', tier: 'l', i: 0 }), new FakeBuffer(2.14, 48000, 2, 'a/l/0'));
    h.ctx.currentTime = 2;
    expect(h.chain.fill(2)).toBeNull();
    expect(h.chain.tail).toBe(6); // 2 + LOOKAHEAD, carried by the scrub tier past u = 2
    expect(h.chain.scheduled.map((x) => x.key.tier)).toEqual(['l', 's', 's']);
    expect(h.chain.coverageStalls).toBe(0);
    expect(h.store.lastError).toBeNull();
  });

  it('fills a well-formed set up to the lookahead', () => {
    const h = chainFor(8);
    expect(h.chain.fill(0)).toBeNull();
    expect(h.chain.tail).toBe(4); // LOOKAHEAD = 4 s: slices 0 and 1
  });
});

describe('Chain.pick cache accounting', () => {
  it('counts one hit or one miss per pick, not a listen-tier miss for every scrub-tier hit', () => {
    const h = chainFor(8);
    for (let n = 0; n < 5; n++) expect(h.chain.pick(0.5)?.key.tier).toBe('s');
    expect(h.store.decoded.hits).toBe(5);
    expect(h.store.decoded.misses).toBe(0);
    h.store.decoded.delete(keyStr({ v: 'a', tier: 's', i: 3 }));
    expect(h.chain.pick(6.5)).toBeNull();
    expect(h.store.decoded.misses).toBe(1);
  });
});
