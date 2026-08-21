import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AUDIO } from '../../src/config';
import { Engine } from '../../src/audio/engine';
import { Fetcher } from '../../src/audio/net/fetcher';
import { segmentAt } from '../../src/audio/scheduler';
import { SegmentStore } from '../../src/audio/store';
import { FakeAudioContext, FakeBuffer, FakeDecoder, FakeFetch, FakeSource, makeSet } from './fakes';

function harness(n = 30, D = 8, opts: { fetchMs?: number; decodeMs?: number; decodedBudget?: number } = {}) {
  const variants = Array.from({ length: n }, (_, i) => `v${i}`);
  const { set, objects } = makeSet(variants, D);
  const ctx = new FakeAudioContext();
  const ff = new FakeFetch(objects);
  ff.latencyMs = opts.fetchMs ?? 0;
  const fetcher = new Fetcher(8, ff.fn, () => Date.now());
  const decoder = new FakeDecoder();
  decoder.latencyMs = opts.decodeMs ?? 0;
  const store = new SegmentStore(set, fetcher, decoder, { decodedBytes: opts.decodedBudget ?? 64 << 20, compressedBytes: 32 << 20 });
  const engine = new Engine(ctx, set, store, () => Date.now());
  engine.setOrder(variants, 0);
  engine.start();
  /** advance wall + audio time together in 10 ms steps */
  const run = async (ms: number) => {
    for (let t = 0; t < ms; t += 10) {
      ctx.advance(0.01);
      await vi.advanceTimersByTimeAsync(10);
    }
  };
  const liveSources = () => ctx.live();
  /** the audio (tag) actually audible: sources whose chain voice gain ≈ 1 */
  const audibleTags = () =>
    liveSources()
      .filter((s) => {
        const g = s.connected[0] as { connected: { gain: { valueAt(t: number): number } }[] };
        const voice = g.connected[0]!;
        return voice.gain.valueAt(ctx.currentTime) > 0.99;
      })
      .map((s) => (s.buffer as FakeBuffer).tag);
  return { set, ctx, ff, fetcher, decoder, store, engine, run, liveSources, audibleTags, variants };
}

describe('Engine', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
  });
  afterEach(() => vi.useRealTimers());

  it('boots, plays the selected variant from position 0 with correct offset', async () => {
    const h = harness();
    h.engine.select('v0');
    h.engine.play();
    await h.run(200);
    expect(h.engine.audible).toBe('v0');
    const live = h.liveSources();
    expect(live.length).toBeGreaterThan(0);
    const first = h.ctx.sources[0]!;
    // first segment started at now+lead with offset lead_in + position ≈ 0.12 + small
    expect(first.started!.offset).toBeGreaterThanOrEqual(0.12);
    expect(first.started!.offset).toBeLessThan(0.2);
    expect(h.engine.status.kind).toBe('playing');
    expect(h.audibleTags().some((t) => t.startsWith('v0/'))).toBe(true);
  });

  it('hot switch keeps the position (±1 ms) and crossfades 1→0 / 0→1 over 10 ms', async () => {
    const h = harness();
    h.engine.select('v0');
    h.engine.play();
    await h.run(500);
    const tSel = h.ctx.currentTime;
    h.engine.select('v1');
    await h.run(50);
    expect(h.engine.audible).toBe('v1');
    const srcB = h.ctx.sources.find((s) => (s.buffer as FakeBuffer).tag.startsWith('v1/'))!;
    // song position at its start time must equal offset - lead_in (segment 0)
    const posAtStart = srcB.started!.when - (tSel + 0); // timeline origin = 0 - (play at ~0)
    const claimed = srcB.started!.offset - 0.12;
    const originPos = h.engine.timeline.position(srcB.started!.when);
    expect(Math.abs(originPos - claimed)).toBeLessThan(0.001);
    expect(posAtStart).toBeGreaterThan(0);
    // voice gains: old ramps to 0, new to 1, both within 10 ms of each other
    const chains = h.engine.pool.chains;
    expect(chains.length).toBe(2);
    const [old, nu] = chains;
    const go = (old!.voice.gain as unknown as { events: { kind: string; value?: number; t: number }[] }).events;
    const gn = (nu!.voice.gain as unknown as { events: { kind: string; value?: number; t: number }[] }).events;
    const oldRamp = go.filter((e) => e.kind === 'ramp').at(-1)!;
    const newRamp = gn.filter((e) => e.kind === 'ramp').at(-1)!;
    expect(oldRamp.value).toBe(0);
    expect(newRamp.value).toBe(1);
    expect(Math.abs(oldRamp.t - newRamp.t)).toBeLessThan(1e-9);
    expect(newRamp.t - srcB.started!.when).toBeCloseTo(AUDIO.SWITCH_XFADE, 6);
    const lat = h.engine.metrics.switchLatencyMs.at(-1)!;
    expect(lat).toBeLessThanOrEqual(20);
  });

  it('never-ready target: audio keeps playing, status loading → wontload after 8 s, old chain untouched', async () => {
    const h = harness(30, 20);
    h.engine.select('v0');
    h.engine.play();
    await h.run(300);
    for (let i = 0; i < 10; i++) h.ff.failUrls.add(`/a/test/g/g1/000${i}.pk`);
    // v25 lives in group 1, none of whose packs are fetched yet
    h.engine.select('v25');
    await h.run(200);
    expect(h.engine.status.kind).toBe('loading');
    expect(h.engine.audible).toBe('v0');
    expect(h.audibleTags().some((t) => t.startsWith('v0/'))).toBe(true);
    await h.run(8200);
    expect(h.engine.status.kind).toBe('wontload');
    expect(h.engine.status.message).toMatch(/won't load — still on v0/);
    expect(h.engine.audible).toBe('v0');
    expect(h.audibleTags().some((t) => t.startsWith('v0/'))).toBe(true);
    expect(h.engine.pool.chains.filter((c) => !c.releasing).length).toBe(1);
  });

  it('recovers: a target that arrives late commits when its bytes finally decode', async () => {
    const h = harness(30, 8, { fetchMs: 400 });
    h.engine.select('v0');
    h.engine.play();
    await h.run(600);
    expect(h.engine.audible).toBe('v0');
    h.engine.select('v26');
    await h.run(100);
    expect(h.engine.status.kind).toBe('loading');
    await h.run(600);
    expect(h.engine.audible).toBe('v26');
    expect(h.engine.status.kind).toBe('playing');
  });

  it('last select wins: a burst of selects commits only the final one (no queueing)', async () => {
    const h = harness(30, 8, { decodeMs: 150 });
    h.engine.select('v0');
    h.engine.play();
    await h.run(400);
    for (let i = 1; i <= 10; i++) h.engine.select(`v${i}`);
    await h.run(400);
    expect(h.engine.audible).toBe('v10');
    // intermediate variants never got a chain
    const started = new Set(h.engine.pool.chains.map((c) => c.variant));
    expect(started.has('v5')).toBe(false);
  });

  it('never evicts the audible chain buffers even with a tiny decoded budget', async () => {
    const h = harness(30, 8, { decodedBudget: 3 * 102720 * 8 }); // ≈ 3 scrub buffers
    h.engine.select('v0');
    h.engine.play();
    await h.run(1000);
    // the prefetcher tried to decode many neighbours; audible's scheduled keys must survive
    const keys = h.engine.audibleChain!.keys;
    expect(keys.length).toBeGreaterThan(0);
    for (const k of keys) expect(h.store.decoded.has(k)).toBe(true);
    expect(h.store.decoded.evictions).toBeGreaterThan(0);
  });

  it('seams: segment k+1 starts 2.5 ms before the boundary at buffer offset lead_in − 2.5 ms', async () => {
    const h = harness(4, 8);
    for (const v of h.variants) h.ff.failUrls.add(`/a/test/l/rh-${v}/000.opus`); // scrub tier only
    h.engine.select('v0');
    h.engine.play();
    await h.run(300);
    const srcs = h.ctx.sources.filter((s) => (s.buffer as FakeBuffer).tag.startsWith('v0/s/'));
    expect(srcs.length).toBeGreaterThanOrEqual(3); // lookahead 4 s → slices 0,1,2
    const s1 = srcs.find((s) => (s.buffer as FakeBuffer).tag === 'v0/s/1')!;
    const origin = h.engine.timeline.timeAt(0);
    expect(s1.started!.when - origin).toBeCloseTo(2 - AUDIO.SEAM_XFADE / 2, 6);
    expect(s1.started!.offset).toBeCloseTo(0.12 - AUDIO.SEAM_XFADE / 2, 6);
    expect(s1.started!.duration).toBeCloseTo(2 + AUDIO.SEAM_XFADE, 6);
    const g1 = s1.connected[0] as { gain: { events: { kind: string; value?: number; t: number }[] } };
    const ramps = g1.gain.events.filter((e) => e.kind === 'ramp');
    expect(ramps[0]!.value).toBe(1);
    expect(ramps[0]!.t - s1.started!.when).toBeCloseTo(AUDIO.SEAM_XFADE, 6);
    expect(ramps.at(-1)!.value).toBe(0);
    expect(ramps.at(-1)!.t - origin).toBeCloseTo(4 + AUDIO.SEAM_XFADE / 2, 6);
  });

  it('seek during a pending commit lands at the new position', async () => {
    const h = harness(30, 8, { fetchMs: 300 });
    h.engine.select('v0');
    h.engine.play();
    await h.run(500);
    h.engine.select('v27');
    await h.run(50);
    expect(h.engine.status.kind).toBe('loading');
    h.engine.seek(5);
    await h.run(600);
    expect(h.engine.audible).toBe('v27');
    const src = h.ctx.sources.filter((s) => (s.buffer as FakeBuffer).tag.startsWith('v27/')).at(0)!;
    // seeked to 5 → slice 2, offset ≈ lead_in + (5 − 4) + a bit
    expect((src.buffer as FakeBuffer).tag).toBe('v27/s/2');
    expect(src.started!.offset).toBeGreaterThanOrEqual(0.12 + 1);
    expect(src.started!.offset).toBeLessThan(0.12 + 1.7);
  });

  it('loops gaplessly: after D the chain continues with slice 0 and the timeline rebases', async () => {
    const h = harness(4, 8);
    h.engine.setLoop(true);
    h.engine.select('v0');
    h.engine.play();
    await h.run(9000);
    expect(h.engine.playing).toBe(true);
    expect(h.engine.position()).toBeGreaterThan(0.5);
    expect(h.engine.position()).toBeLessThan(1.5);
    const starts = h.ctx.sources.map((s) => ({ tag: (s.buffer as FakeBuffer).tag, when: s.started!.when }));
    const origin = h.engine.timeline.timeAt(0); // after rebase: start of the current loop
    // a segment for the second pass must have been scheduled exactly at origin − 2.5 ms (seam) and be slice 0
    const second = starts.filter((s) => Math.abs(s.when - (origin - AUDIO.SEAM_XFADE / 2)) < 1e-6);
    expect(second.length).toBe(1);
    expect(second[0]!.tag).toMatch(/^v0\/(s|l)\/0$/);
    expect(origin).toBeGreaterThanOrEqual(8); // second pass began after one full loop of 8 s (play() at t=0)
    expect(h.engine.status.kind).toBe('playing');
  });

  it('stops at the end when not looping', async () => {
    const h = harness(4, 8);
    h.engine.select('v0');
    h.engine.play();
    await h.run(8500);
    expect(h.engine.playing).toBe(false);
    expect(h.engine.status.kind).toBe('ended');
  });

  it('upgrades to the listen tier once settled, aligned on the same timeline', async () => {
    const h = harness(4, 8);
    h.engine.select('v0');
    h.engine.play();
    await h.run(1200);
    const l = h.ctx.sources.find((s) => (s.buffer as FakeBuffer).tag === 'v0/l/0');
    expect(l).toBeDefined();
    expect(h.engine.audibleChain!.preferTier).toBe('l');
    // listen segment started at song position p with offset lead_in + p
    const p = h.engine.timeline.position(l!.started!.when);
    expect(Math.abs(l!.started!.offset - (0.12 + p))).toBeLessThan(0.001);
    // only one non-releasing chain, and it is the listen one
    const liveChains = h.engine.pool.chains.filter((c) => !c.releasing);
    expect(liveChains.length).toBe(1);
    expect(liveChains[0]!.lastTier).toBe('l');
  });

  it('voice pool stays bounded under rapid switching and steals only releasing chains', async () => {
    const h = harness(30, 8);
    h.engine.select('v0');
    h.engine.play();
    await h.run(200);
    for (let i = 1; i < 25; i++) {
      h.engine.select(`v${i}`);
      await h.run(10);
    }
    expect(h.engine.pool.size).toBeLessThanOrEqual(AUDIO.VOICES);
    const audible = h.engine.audibleChain!;
    expect(h.engine.pool.chains.includes(audible)).toBe(true);
    expect(audible.releasing).toBe(false);
  });

  it('pause releases the chain; play resumes at the paused position', async () => {
    const h = harness(4, 8);
    h.engine.select('v0');
    h.engine.play();
    await h.run(1000);
    h.engine.pause();
    const pos = h.engine.position();
    await h.run(500);
    expect(h.engine.position()).toBeCloseTo(pos, 3);
    expect(h.liveSources().length).toBe(0);
    h.engine.play();
    await h.run(100);
    expect(h.engine.playing).toBe(true);
    const last = h.ctx.sources.at(-1)!;
    const seg = segmentAt(h.set, (last.buffer as FakeBuffer).tag.includes('/l/') ? 'l' : 's', pos);
    expect(last.started!.offset).toBeCloseTo(0.12 + (pos - seg.uStart) + (last.started!.when - h.engine.timeline.timeAt(pos)), 2);
  });
});

describe('Prefetcher via Engine', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
  });
  afterEach(() => vi.useRealTimers());

  it('fetches whole packs around the cursor and dedups pack requests', async () => {
    const h = harness(60, 8);
    h.engine.select('v0');
    h.engine.play();
    await h.run(600);
    const packFetches = h.ff.log.filter((l) => l.url.endsWith('.pk') && !l.range);
    const urls = packFetches.map((l) => l.url);
    expect(new Set(urls).size).toBe(urls.length); // no duplicate whole-pack fetches
    expect(urls.some((u) => u.includes('/g/g0/0000.pk'))).toBe(true);
    expect(urls.some((u) => u.includes('/g/g0/0001.pk'))).toBe(true);
    expect(h.store.stats.wholePacks).toBeGreaterThan(0);
  });

  it('radius grows with cursor velocity and is clamped', async () => {
    const h = harness(60, 8);
    h.engine.select('v0');
    h.engine.play();
    expect(h.engine.prefetcher.radius).toBe(4);
    for (let i = 1; i < 40; i++) {
      h.engine.cursor(i);
      await h.run(30);
    }
    expect(h.engine.prefetcher.radius).toBeGreaterThan(4);
    expect(h.engine.prefetcher.radius).toBeLessThanOrEqual(64);
    await h.run(3000);
    h.engine.cursor(40);
    await h.run(2500);
    h.engine.cursor(40);
    expect(h.engine.prefetcher.radius).toBe(4);
  });

  it('uses Range-per-member when fewer than 6 members of a pack are wanted', async () => {
    const h = harness(60, 8);
    // cursor at the end of group 0: only a handful of group-1 members fall inside R=4
    h.engine.setOrder(h.variants, 23);
    h.engine.select('v23');
    h.engine.play();
    await h.run(600);
    const ranges = h.ff.log.filter((l) => l.url.includes('/g/g1/') && l.range);
    expect(ranges.length).toBeGreaterThan(0);
    expect(h.store.stats.rangeMembers).toBeGreaterThan(0);
  });
});
