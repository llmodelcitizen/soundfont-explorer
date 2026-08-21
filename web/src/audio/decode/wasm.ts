import type { BufferLike, ContextLike } from '../types';
import { DecodeQueue, type Decoder } from './index';

interface WorkerLike {
  postMessage(msg: unknown, transfer?: Transferable[]): void;
  onmessage: ((ev: MessageEvent) => void) | null;
  terminate(): void;
}

export type WorkerFactory = () => WorkerLike;

const defaultFactory: WorkerFactory = () => new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' }) as unknown as WorkerLike;

/** Pool of N decode workers fed from a priority queue. */
export class WasmDecoder implements Decoder {
  readonly kind = 'wasm' as const;
  readonly stats = { decoded: 0, msTotal: 0, errors: 0, queued: 0, active: 0 };
  private workers: { w: WorkerLike; busy: boolean }[] = [];
  private q: DecodeQueue;
  private nextId = 1;
  private waiting = new Map<number, { resolve: (v: { channelData: Float32Array[]; sampleRate: number }) => void; reject: (e: unknown) => void }>();

  constructor(private readonly ctx: ContextLike, poolSize = 4, factory: WorkerFactory = defaultFactory) {
    for (let i = 0; i < poolSize; i++) {
      const w = factory();
      const slot = { w, busy: false };
      w.onmessage = (ev: MessageEvent) => {
        const { id, error, channelData, sampleRate } = ev.data as { id: number; error?: string; channelData?: Float32Array[]; sampleRate?: number };
        const p = this.waiting.get(id);
        this.waiting.delete(id);
        slot.busy = false;
        if (!p) return;
        if (error || !channelData) p.reject(new Error(error ?? 'decode failed'));
        else p.resolve({ channelData, sampleRate: sampleRate ?? 48000 });
      };
      this.workers.push(slot);
    }
    this.q = new DecodeQueue(poolSize);
  }

  decode(bytes: ArrayBuffer, priority: number): Promise<BufferLike> {
    return this.q.submit(priority, async () => {
      const slot = this.workers.find((s) => !s.busy);
      if (!slot) throw new Error('no free decode worker'); // cannot happen: queue concurrency == pool size
      slot.busy = true;
      const id = this.nextId++;
      const t0 = Date.now();
      try {
        const res = await new Promise<{ channelData: Float32Array[]; sampleRate: number }>((resolve, reject) => {
          this.waiting.set(id, { resolve, reject });
          // copy: the compressed cache keeps the original
          slot.w.postMessage({ id, bytes: bytes.slice(0) }, []);
        });
        const channels = Math.max(1, res.channelData.length);
        const length = res.channelData[0]?.length ?? 0;
        const buf = this.ctx.createBuffer(channels, length, res.sampleRate);
        res.channelData.forEach((c, i) => buf.copyToChannel(c, i));
        this.stats.decoded++;
        this.stats.msTotal += Date.now() - t0;
        return buf;
      } catch (e) {
        this.stats.errors++;
        slot.busy = false;
        throw e;
      } finally {
        this.stats.queued = this.q.queued;
        this.stats.active = this.q.running;
      }
    });
  }

  dispose(): void {
    for (const s of this.workers) s.w.terminate();
    this.workers = [];
  }
}
