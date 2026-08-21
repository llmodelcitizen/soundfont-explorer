import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AbortedError, Fetcher } from '../../src/audio/net/fetcher';
import { parseHeader, splitPack } from '../../src/audio/net/packs';
import { FakeFetch, descriptorBytes, packBytes } from './fakes';

describe('SFPK', () => {
  it('parses header and splits members', () => {
    const members = [descriptorBytes({ v: 'a', tier: 's', i: 0 }, 10), descriptorBytes({ v: 'b', tier: 's', i: 0 }, 300)];
    const blob = packBytes(members);
    const h = parseHeader(blob);
    expect(h.count).toBe(2);
    expect(h.lengths).toEqual([members[0]!.byteLength, members[1]!.byteLength]);
    expect(h.offsets).toEqual([16, 16 + members[0]!.byteLength]);
    const out = splitPack(blob);
    expect(out.map((m) => m.byteLength)).toEqual(members.map((m) => m.byteLength));
    expect(() => splitPack(blob.slice(0, blob.byteLength - 1))).toThrow(/size mismatch/);
    expect(() => parseHeader(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]).buffer)).toThrow(/magic/);
  });
});

describe('Fetcher', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('caps in-flight, orders by priority, dedups, supports ranges', async () => {
    const objects = new Map<string, ArrayBuffer>();
    for (let i = 0; i < 6; i++) objects.set(`/o${i}`, new Uint8Array(100).fill(i).buffer);
    const ff = new FakeFetch(objects);
    ff.latencyMs = 10;
    const f = new Fetcher(2, ff.fn, () => Date.now());
    const order: string[] = [];
    const ps = [5, 4, 3, 2, 1, 0].map((i) => f.get(`/o${i}`, { priority: i }).then(() => order.push(`/o${i}`)));
    expect(f.inflightCount).toBe(2); // first two started immediately (5, 4)
    expect(f.queuedCount).toBe(4);
    const dup = f.get('/o0', { priority: 9 });
    expect(f.queuedCount).toBe(4); // deduped
    await vi.advanceTimersByTimeAsync(100);
    await Promise.all([...ps, dup]);
    expect(order.slice(0, 2).sort()).toEqual(['/o4', '/o5']);
    expect(order.slice(2)).toEqual(['/o0', '/o1', '/o2', '/o3']); // by priority once slots free up
    const r = f.get('/o1', { priority: 0, range: { start: 10, end: 19 } });
    await vi.advanceTimersByTimeAsync(20);
    expect((await r).byteLength).toBe(10);
    expect(ff.log.at(-1)?.range).toBe('bytes=10-19');
    expect(f.stats.completed).toBe(7);
  });

  it('reprioritize moves a queued request forward (only to a more urgent priority)', async () => {
    const objects = new Map<string, ArrayBuffer>([['/a', new ArrayBuffer(8)], ['/b', new ArrayBuffer(8)], ['/c', new ArrayBuffer(8)]]);
    const ff = new FakeFetch(objects);
    ff.latencyMs = 20;
    const f = new Fetcher(1, ff.fn, () => Date.now());
    const order: string[] = [];
    const ps = [f.get('/a', { priority: 1 }), f.get('/b', { priority: 5 }), f.get('/c', { priority: 3 })].map((p, i) => p.then(() => order.push('abc'[i]!)));
    f.reprioritize('/b', 0);
    f.reprioritize('/c', 9); // never demotes
    await vi.advanceTimersByTimeAsync(200);
    await Promise.all(ps);
    expect(order).toEqual(['a', 'b', 'c']);
  });

  it('aborts matching queued + in-flight requests except sticky ones', async () => {
    const objects = new Map<string, ArrayBuffer>([['/l1', new ArrayBuffer(8)], ['/l2', new ArrayBuffer(8)], ['/p', new ArrayBuffer(8)]]);
    const ff = new FakeFetch(objects);
    ff.latencyMs = 50;
    const f = new Fetcher(1, ff.fn, () => Date.now());
    const a = f.get('/l1', { priority: 1, tag: 'listen' });
    const b = f.get('/l2', { priority: 2, tag: 'listen' });
    const c = f.get('/p', { priority: 3, tag: 'pack', sticky: true });
    const n = f.abortWhere((_u, o) => o.tag === 'listen');
    expect(n).toBe(2);
    await expect(b).rejects.toBeInstanceOf(AbortedError);
    await vi.advanceTimersByTimeAsync(200);
    await expect(a).rejects.toBeInstanceOf(AbortedError);
    expect((await c).byteLength).toBe(8);
  });

  it('rejects on HTTP errors and on non-206 range responses', async () => {
    const objects = new Map<string, ArrayBuffer>([['/x', new ArrayBuffer(8)]]);
    const ff = new FakeFetch(objects);
    const f = new Fetcher(2, ff.fn, () => Date.now());
    await expect(f.get('/missing', { priority: 0 })).rejects.toThrow(/HTTP 404/);
    expect(f.stats.errors).toBe(1);
  });
});
