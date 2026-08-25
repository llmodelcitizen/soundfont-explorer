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
});
