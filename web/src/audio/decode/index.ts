import type { BufferLike, ContextLike } from '../types';

export interface Decoder {
  readonly kind: 'native' | 'wasm';
  decode(bytes: ArrayBuffer, priority: number): Promise<BufferLike>;
  readonly stats: { decoded: number; msTotal: number; errors: number; queued: number; active: number };
  dispose?(): void;
}

/** Small priority queue bounding concurrent decodes (native or worker). */
export class DecodeQueue {
  private queue: { priority: number; seq: number; run: () => Promise<void> }[] = [];
  private active = 0;
  private seq = 0;

  constructor(private readonly concurrency: number) {}

  get queued(): number {
    return this.queue.length;
  }
  get running(): number {
    return this.active;
  }

  submit<T>(priority: number, fn: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.queue.push({
        priority,
        seq: this.seq++,
        run: () => fn().then(resolve, reject),
      });
      this.queue.sort((a, b) => a.priority - b.priority || a.seq - b.seq);
      this.pump();
    });
  }

  private pump(): void {
    while (this.active < this.concurrency && this.queue.length) {
      const job = this.queue.shift()!;
      this.active++;
      job.run().finally(() => {
        this.active--;
        this.pump();
      });
    }
  }
}

export { NativeDecoder } from './native';
export { WasmDecoder } from './wasm';

/**
 * Probe: decode a 40 ms 1 kHz Ogg Opus tone natively; accept the native path iff the
 * duration is right and the signal has sane energy. Otherwise use the WASM worker pool.
 */
export async function probeNative(ctx: ContextLike, probeBytes: ArrayBuffer, expectSeconds = 0.04): Promise<{ ok: boolean; reason: string; ms: number }> {
  const t0 = Date.now();
  try {
    const buf = await ctx.decodeAudioData(probeBytes.slice(0));
    const ms = Date.now() - t0;
    if (Math.abs(buf.duration - expectSeconds) > 0.006) return { ok: false, reason: `duration ${buf.duration.toFixed(4)}s`, ms };
    const ch = (buf as unknown as { getChannelData?: (c: number) => Float32Array }).getChannelData?.(0);
    if (ch) {
      let sum = 0;
      for (let i = 0; i < ch.length; i++) sum += ch[i]! * ch[i]!;
      const rms = Math.sqrt(sum / Math.max(1, ch.length));
      if (!(rms > 0.01 && rms < 1)) return { ok: false, reason: `rms ${rms.toFixed(4)}`, ms };
    }
    return { ok: true, reason: 'native', ms };
  } catch (e) {
    return { ok: false, reason: `decodeAudioData rejected: ${(e as Error)?.message ?? e}`, ms: Date.now() - t0 };
  }
}
