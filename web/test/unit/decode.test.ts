import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MAX_CONSECUTIVE_FAILURES, WasmDecoder } from '../../src/audio/decode/wasm';
import { FakeAudioContext, flush } from './fakes';

/** a worker stand-in: the test decides when (and whether) it answers or crashes */
class FakeWorker {
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  terminated = false;
  posted: { id: number }[] = [];
  constructor(private readonly onPost: (w: FakeWorker, id: number) => void) {}
  postMessage(msg: unknown): void {
    const { id } = msg as { id: number };
    this.posted.push({ id });
    this.onPost(this, id);
  }
  terminate(): void {
    this.terminated = true;
  }
  crash(): void {
    this.onerror?.({});
  }
  answer(id: number): void {
    this.onmessage?.({ data: { id, channelData: [new Float32Array(48)], sampleRate: 48000 } } as MessageEvent);
  }
}

function pool(behaviour: (w: FakeWorker, id: number) => void, size = 2) {
  const spawned: FakeWorker[] = [];
  const factory = () => {
    const w = new FakeWorker(behaviour);
    spawned.push(w);
    return w;
  };
  return { spawned, decoder: new WasmDecoder(new FakeAudioContext(), size, factory) };
}

describe('WasmDecoder worker pool', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('gives up on a slot whose replacements keep crashing instead of respawning forever', async () => {
    // the worker script cannot even load: every worker fires onerror right after creation
    const { spawned, decoder } = pool(() => undefined);
    for (const w of spawned) queueMicrotask(() => w.crash());
    // keep crashing whatever gets spawned, as a broken script would
    for (let round = 0; round < 40; round++) {
      await flush(3);
      for (const w of spawned) if (!w.terminated) w.crash();
    }
    // pool of 2, each allowed MAX_CONSECUTIVE_FAILURES replacements, then dropped
    expect(spawned.length).toBeLessThanOrEqual(2 * (1 + MAX_CONSECUTIVE_FAILURES));
    expect(spawned.every((w) => w.terminated)).toBe(true);
    expect(decoder.stats.errors).toBe(2 * (1 + MAX_CONSECUTIVE_FAILURES));
    await expect(decoder.decode(new ArrayBuffer(8), 0)).rejects.toThrow(/no decode workers/);
  });

  it('a crash mid-decode fails that job, and a replacement that answers resets the count', async () => {
    let crashes = 0;
    const { spawned, decoder } = pool((w, id) => {
      if (crashes < MAX_CONSECUTIVE_FAILURES) {
        crashes++;
        queueMicrotask(() => w.crash());
      } else queueMicrotask(() => w.answer(id));
    }, 1);
    for (let i = 0; i < MAX_CONSECUTIVE_FAILURES; i++) {
      await expect(decoder.decode(new ArrayBuffer(8), 0)).rejects.toThrow(/worker error/);
    }
    const buf = await decoder.decode(new ArrayBuffer(8), 0);
    expect(buf.length).toBe(48);
    expect(spawned.length).toBe(1 + MAX_CONSECUTIVE_FAILURES);
    // it answered: the slot is healthy again and survives a further crash without being dropped
    const alive = spawned.at(-1)!;
    alive.crash();
    await flush();
    expect(spawned.length).toBe(2 + MAX_CONSECUTIVE_FAILURES);
    expect(decoder.stats.decoded).toBe(1);
  });

  it('a timeout followed by the crash it caused replaces the worker once, not twice', async () => {
    const { spawned, decoder } = pool(() => undefined, 1);
    const p = decoder.decode(new ArrayBuffer(8), 0);
    p.catch(() => undefined);
    await vi.advanceTimersByTimeAsync(15_001);
    await expect(p).rejects.toThrow(/timeout/);
    expect(spawned.length).toBe(2);
    spawned[0]!.crash(); // the terminated worker's own error event arrives late
    await flush();
    expect(spawned.length).toBe(2);
  });
});
