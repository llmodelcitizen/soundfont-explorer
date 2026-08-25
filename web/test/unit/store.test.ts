import { describe, expect, it } from 'vitest';
import { Fetcher } from '../../src/audio/net/fetcher';
import { SegmentStore } from '../../src/audio/store';
import { packUrl } from '../../src/contracts/set';
import { FakeDecoder, FakeFetch, flush, makeSet } from './fakes';

function storeFor(variants: string[], D = 8) {
  const { set, objects } = makeSet(variants, D);
  const ff = new FakeFetch(objects);
  let now = 0;
  const clock = () => now;
  const store = new SegmentStore(set, new Fetcher(8, ff.fn, clock), new FakeDecoder(), { decodedBytes: 8 << 20, compressedBytes: 8 << 20 }, clock);
  return { set, ff, store, tick: (ms: number) => (now += ms) };
}

describe('SegmentStore', () => {
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
