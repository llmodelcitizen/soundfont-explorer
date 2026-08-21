import type { BufferLike, ContextLike } from '../types';
import { DecodeQueue, type Decoder } from './index';

export class NativeDecoder implements Decoder {
  readonly kind = 'native' as const;
  readonly stats = { decoded: 0, msTotal: 0, errors: 0, queued: 0, active: 0 };
  private q: DecodeQueue;

  constructor(private readonly ctx: ContextLike, concurrency = 4) {
    this.q = new DecodeQueue(concurrency);
  }

  decode(bytes: ArrayBuffer, priority: number): Promise<BufferLike> {
    return this.q.submit(priority, async () => {
      const t0 = Date.now();
      try {
        // decodeAudioData detaches the buffer: hand it a copy so the compressed cache keeps its bytes
        const buf = await this.ctx.decodeAudioData(bytes.slice(0));
        this.stats.decoded++;
        this.stats.msTotal += Date.now() - t0;
        return buf;
      } catch (e) {
        this.stats.errors++;
        throw e;
      } finally {
        this.stats.queued = this.q.queued;
        this.stats.active = this.q.running;
      }
    });
  }
}
