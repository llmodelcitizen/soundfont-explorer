import { afterEach, describe, expect, it, vi } from 'vitest';
import { Fetcher } from '../../src/audio/net/fetcher';
import { SegmentStore } from '../../src/audio/store';
import { packUrl } from '../../src/contracts/set';
import { FakeDecoder, FakeFetch, flush, makeSet } from './fakes';

function storeFor(variants: string[], D = 8) {
  const { set, objects } = makeSet(variants, D);
  const ff = new FakeFetch(objects);
  const decoder = new FakeDecoder();
  let now = 0;
  const clock = () => now;
  const store = new SegmentStore(set, new Fetcher(8, ff.fn, clock), decoder, { decodedBytes: 8 << 20, compressedBytes: 8 << 20 }, clock);
  return { set, ff, decoder, store, tick: (ms: number) => (now += ms) };
}

describe('SegmentStore', () => {
  afterEach(() => vi.useRealTimers());

  it('fetch-only wants enter the negative cache: a missing pack is not re-fetched every tick', async () => {
    const h = storeFor(['a', 'b']);
    const missing = packUrl(h.set, 'g0', 0);
    h.ff.failUrls.add(missing);
    const key = { v: 'a', tier: 's' as const, i: 0 };
    for (let i = 0; i < 10; i++) {
      h.store.want([{ key, priority: 5, fetchOnly: true }]);
      await flush(20);
      h.tick(60);
    }
    expect(h.ff.log.filter((l) => l.url === missing).length).toBe(1);
    expect(h.store.backoffMs(key)).toBeGreaterThan(0);
    expect(h.store.lastError).toMatch(/a\/s\/0/);
  });

  it('a fetch-only success clears the key\u2019s failure count instead of ratcheting the backoff', async () => {
    // a key that only ever travels the fetch-only prefetch path must reset like request() does,
    // or unrelated transient errors walk the backoff towards the 30 s cap over a session
    const h = storeFor(['a', 'b']);
    const url = packUrl(h.set, 'g0', 0);
    const key = { v: 'a', tier: 's' as const, i: 0 };
    const want = async () => {
      h.store.want([{ key, priority: 5, fetchOnly: true }]);
      await flush(20);
    };
    h.ff.failUrls.add(url);
    await want();
    expect(h.store.backoffMs(key)).toBe(1000); // first failure: base backoff
    h.tick(1000);
    h.ff.failUrls.delete(url);
    await want();
    expect(h.store.backoffMs(key)).toBe(0); // fetched fine: the key is clean again
    h.store.compressed.clear();
    h.ff.failUrls.add(url);
    await want();
    expect(h.store.backoffMs(key)).toBe(1000); // a later failure starts over, not at 2000
  });

  it('records one failure per failed request, not one per prefetch tick', async () => {
    // want() runs every AUDIO.RETRY_TICK_MS (60 ms) and none of its guards sees a byte-only
    // fetch in flight, so every tick used to attach another rejection handler to the same
    // Fetcher promise: one failed request counted as ten, walking the exponential backoff to
    // its 30 s cap — and that is the cache the audible path consults, so a transient prefetch
    // miss on a distant neighbour silenced the segment long enough to time the switch out.
    vi.useFakeTimers();
    const h = storeFor(['a', 'b']);
    const missing = packUrl(h.set, 'g0', 0);
    h.ff.failUrls.add(missing);
    h.ff.latencyMs = 600; // ten prefetch ticks fit inside the one request
    const key = { v: 'a', tier: 's' as const, i: 0 };
    // the store clock stays put so backoffMs() reads back the recorded wait, not what is left of it
    for (let t = 0; t <= 660; t += 60) {
      h.store.want([{ key, priority: 5, fetchOnly: true }]);
      await vi.advanceTimersByTimeAsync(60);
    }
    await flush(20);
    expect(h.ff.log.filter((l) => l.url === missing).length).toBe(1);
    expect(h.store.backoffMs(key)).toBe(1000); // one failure → base backoff, not the 30 s cap
    await expect(h.store.request(key, 0)).rejects.toThrow('backoff a/s/0 for 1000 ms');
  });

  it('a successful byte fetch clears a fetch failure but not a decode failure', async () => {
    // bytes that arrive fine and will not decode (a corrupt member) must keep ratcheting:
    // clearing the count on the byte fetch alone let the prefetcher reset it every time the
    // compressed bytes aged out, so the audible path retried the bad decode at ~1 Hz forever
    const h = storeFor(['a', 'b']);
    const key = { v: 'a', tier: 's' as const, i: 0 };
    h.decoder.failKeys.add('a/s/0');
    await expect(h.store.request(key, 0)).rejects.toThrow(/decode failed/);
    expect(h.store.backoffMs(key)).toBe(1000);
    h.tick(1000);
    h.store.compressed.clear(); // the compressed bytes aged out under cache pressure
    h.store.want([{ key, priority: 5, fetchOnly: true }]);
    await flush(20);
    expect(h.store.compressed.has(`${packUrl(h.set, 'g0', 0)}#0`)).toBe(true); // the fetch worked
    await expect(h.store.request(key, 0)).rejects.toThrow(/decode failed/);
    expect(h.store.backoffMs(key)).toBe(2000); // second decode failure: the ratchet still climbs
  });

  it('re-splits a pack whose member was evicted before its waiter woke up', async () => {
    // the waiters behind the first one wake in later microtasks; cache pressure from another
    // pack in between must not turn into 'member N missing from pack' (which the negative cache
    // would then hold against a key whose bytes we are still holding)
    const ids = Array.from({ length: 8 }, (_, i) => `v${i}`);
    const h = storeFor(ids);
    const url = packUrl(h.set, 'g0', 0);
    const lru = h.store.compressed;
    const set = lru.set.bind(lru);
    let evicted = false;
    lru.set = (k, v) => {
      set(k, v);
      if (!evicted && k === `${url}#7`) {
        evicted = true; // once: right after the split, before member 1's waiter runs
        lru.delete(`${url}#1`);
      }
    };
    // priority 0 is urgent, so every request takes the whole pack and shares the one fetch
    const settled = await Promise.allSettled(ids.map((v) => h.store.request({ v, tier: 's', i: 0 }, 0)));
    expect(settled.map((r) => r.status)).toEqual(ids.map(() => 'fulfilled'));
    expect(h.store.stats.wholePacks).toBe(1); // the same bytes split twice are still one pack
    expect(h.store.lastError).toBeNull();
  });

  it('splits a whole pack once, however many members were waiting on the same fetch', async () => {
    const ids = Array.from({ length: 24 }, (_, i) => `v${i}`);
    const h = storeFor(ids);
    // ≥ wholePackThreshold members of one pack → a single whole-pack fetch shared by all waiters
    h.store.want(ids.slice(0, 8).map((v, j) => ({ key: { v, tier: 's' as const, i: 0 }, priority: 1 + j })));
    await flush(40);
    expect(h.ff.log.filter((l) => l.url === packUrl(h.set, 'g0', 0) && !l.range).length).toBe(1);
    expect(h.store.stats.wholePacks).toBe(1);
    expect(h.store.stats.decodedOk).toBe(8);
    for (const v of ids.slice(0, 8)) expect(h.store.peek({ v, tier: 's', i: 0 })).toBeDefined();
    // a later re-fetch of the same pack (new bytes) is split again
    h.store.compressed.clear();
    h.store.decoded.clear();
    h.store.want(ids.slice(0, 8).map((v) => ({ key: { v, tier: 's' as const, i: 0 }, priority: 1 })));
    await flush(40);
    expect(h.store.stats.wholePacks).toBe(2);
  });

  it('gives every waiter on an unparseable pack the parse error, not \u2018member missing\u2019', async () => {
    // the pack is marked ingested only after the split succeeds, so the seven waiters that wake
    // up behind the first one do not skip the split and report a misleading missing-member error
    const ids = Array.from({ length: 8 }, (_, i) => `v${i}`);
    const { set, objects } = makeSet(ids);
    const url = packUrl(set, 'g0', 0);
    objects.set(url, objects.get(url)!.slice(0, -1)); // truncated: splitPack() throws on the size
    const store = new SegmentStore(set, new Fetcher(8, new FakeFetch(objects).fn, () => 0), new FakeDecoder(), { decodedBytes: 8 << 20, compressedBytes: 8 << 20 }, () => 0);
    // priority 0 is urgent, so every request takes the whole pack and shares the one fetch
    const settled = await Promise.allSettled(ids.map((v) => store.request({ v, tier: 's', i: 0 }, 0)));
    expect(settled.every((r) => r.status === 'rejected')).toBe(true);
    const reasons = settled.map((r) => (r.status === 'rejected' ? String((r.reason as Error).message) : ''));
    expect(reasons.filter((m) => /SFPK: size mismatch/.test(m)).length).toBe(8);
    expect(reasons.filter((m) => /missing from pack/.test(m))).toEqual([]);
    expect(store.lastError).toMatch(/SFPK: size mismatch/);
    expect(store.stats.wholePacks).toBe(0); // nothing was ingested
  });
});
