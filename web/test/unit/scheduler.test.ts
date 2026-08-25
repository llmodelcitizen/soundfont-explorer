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

describe('Chain.fill', () => {
  it('stops instead of spinning when the set\'s slices do not cover its duration', () => {
    const h = chainFor(8);
    h.set.slices = 2; // malformed: 2 × 2 s slices for an 8 s song → every u ≥ 4 maps to the last slice, which ends at 4
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
