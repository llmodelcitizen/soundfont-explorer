/**
 * Cost-ordered prefetch (plan §10): want-set = packs covering the filtered order within a
 * velocity-scaled radius around the cursor for the current and next slice(s); settled audible
 * variant gets its listen slices. Packs are never aborted; listen fetches beyond 2R are.
 */
import { NET } from '../config';
import type { SetDoc } from '../contracts/set';
import { listenUrl } from '../contracts/set';
import type { SegmentStore, Want } from './store';

export const PRIO = {
  URGENT: 0,
  AUDIBLE_LISTEN: 0.5,
  AUDIBLE_NEXT: 0.75,
} as const;

export class Prefetcher {
  private lastIndex = 0;
  private lastMoveAt = 0;
  /** rows per second, EMA */
  velocity = 0;
  direction = 1;
  radius: number = NET.radiusMin;
  lastWants = 0;

  constructor(
    private readonly store: SegmentStore,
    private readonly set: SetDoc,
    private getOrder: () => string[],
  ) {}

  setOrder(getOrder: () => string[]): void {
    this.getOrder = getOrder;
  }

  /** Called whenever the cursor (selection) moves; `now` in ms. */
  cursor(index: number, now: number): void {
    const dt = (now - this.lastMoveAt) / 1000;
    const di = index - this.lastIndex;
    if (di !== 0) this.direction = Math.sign(di);
    if (this.lastMoveAt > 0 && dt > 0 && dt < 2) {
      const inst = Math.abs(di) / dt;
      this.velocity = this.velocity * 0.6 + inst * 0.4;
    } else if (dt >= 2) {
      this.velocity = 0;
    }
    this.lastIndex = index;
    this.lastMoveAt = now;
    this.radius = Math.min(NET.radiusMax, Math.max(NET.radiusMin, Math.ceil(this.velocity * NET.radiusSeconds)));
  }

  decay(now: number): void {
    if (now - this.lastMoveAt > 1000) {
      this.velocity *= 0.5;
      this.radius = Math.min(NET.radiusMax, Math.max(NET.radiusMin, Math.ceil(this.velocity * NET.radiusSeconds)));
    }
  }

  /** Compute and issue the want-set. `pos` = song position (s); audible = settled audible variant or null. */
  tick(pos: number, audible: string | null, settled: boolean): Want[] {
    const order = this.getOrder();
    if (!order.length) return [];
    const i = Math.min(Math.max(this.lastIndex, 0), order.length - 1);
    const R = this.radius;
    const s0 = Math.min(Math.floor(pos / this.set.slice_s), this.set.slices - 1);
    const inSlice = pos - s0 * this.set.slice_s;
    const slices = [s0, s0 + 1];
    if (inSlice > this.set.slice_s / 2) slices.push(s0 + 2);
    const wants: Want[] = [];
    for (let j = Math.max(0, i - R); j <= Math.min(order.length - 1, i + R); j++) {
      const v = order[j]!;
      const dirMatch = j === i || Math.sign(j - i) === this.direction;
      const cost = Math.abs(j - i) * (dirMatch ? 1 : NET.directionPenalty) + 1;
      slices.forEach((s, n) => {
        const si = this.set.slices > 0 ? s % this.set.slices : s;
        wants.push({ key: { v, tier: 's', i: si }, priority: cost + n * 0.25 });
      });
    }
    if (audible && settled && this.set.listen.slices > 0) {
      const k = Math.min(Math.floor(pos / this.set.listen.slice_s), this.set.listen.slices - 1);
      wants.push({ key: { v: audible, tier: 'l', i: k }, priority: PRIO.AUDIBLE_LISTEN });
      const k1 = (k + 1) % this.set.listen.slices;
      wants.push({ key: { v: audible, tier: 'l', i: k1 }, priority: PRIO.AUDIBLE_NEXT });
      // abort listen fetches that are no longer the audible variant's
      const rh = this.set.variants[audible]?.render_hash;
      const keep = new Set<string>();
      if (rh) {
        keep.add(listenUrl(this.set, rh, k));
        keep.add(listenUrl(this.set, rh, k1));
      }
      this.store.abortListen(keep);
    } else if (!audible || !settled) {
      this.store.abortListen(new Set());
    }
    this.lastWants = wants.length;
    this.store.want(wants);
    return wants;
  }
}
