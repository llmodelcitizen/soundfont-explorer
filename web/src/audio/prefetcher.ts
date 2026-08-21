/**
 * Cost-ordered prefetch (plan §10): want-set = packs covering the filtered order within a
 * velocity-scaled radius around the cursor for the current and next slice(s); settled audible
 * variant gets its listen slices. Packs are never aborted; listen fetches beyond 2R are.
 */
import { CACHE, NET } from '../config';
import type { SetDoc } from '../contracts/set';
import { listenUrl } from '../contracts/set';
import type { SegmentStore, Want } from './store';

/** decoded bytes of one scrub segment (f32 stereo at the set's sample count) */
const segBytes = (set: SetDoc): number => set.segment_samples * 2 * 4;

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

  constructor(
    private readonly store: SegmentStore,
    private readonly set: SetDoc,
    private readonly getOrder: () => string[],
  ) {}

  /**
   * Decode radius: how many neighbours (each side) we can keep decoded without thrashing the
   * decoded LRU: ≈ 60 % of the budget for 3 slices per neighbour (the rest: listen tier + pins).
   */
  decodeRadius(budgetBytes: number = CACHE.decodedBytes): number {
    const perNeighbour = 3 * segBytes(this.set);
    return Math.max(1, Math.floor((0.6 * budgetBytes) / perNeighbour / 2));
  }

  /** Called whenever the cursor (selection) moves; `now` in ms. `jump` = not a scrub step (no velocity). */
  cursor(index: number, now: number, jump = false): void {
    const dt = (now - this.lastMoveAt) / 1000;
    const di = index - this.lastIndex;
    if (jump) {
      this.lastIndex = index;
      this.lastMoveAt = now;
      return;
    }
    if (di !== 0) this.direction = Math.sign(di);
    if (this.lastMoveAt > 0 && dt > 0 && dt < 2) {
      const inst = Math.abs(di) / dt;
      this.velocity = this.velocity * 0.6 + inst * 0.4;
    } else if (dt >= 2) {
      this.velocity = 0;
    }
    this.lastIndex = index;
    this.lastMoveAt = now;
    this.updateRadius();
  }

  decay(now: number): void {
    if (now - this.lastMoveAt > 1000) {
      this.velocity *= 0.5;
      this.updateRadius();
    }
  }

  /** velocity-scaled prefetch radius, clamped to [radiusMin, radiusMax] */
  private updateRadius(): void {
    this.radius = Math.min(NET.radiusMax, Math.max(NET.radiusMin, Math.ceil(this.velocity * NET.radiusSeconds)));
  }

  /**
   * Compute and issue the want-set. `pos` = song position (s); `audible` = the variant currently
   * playing (its listen fetches are always kept); listen slices are only *added* once settled.
   */
  tick(pos: number, audible: string | null, settled: boolean, loop = false): Want[] {
    const order = this.getOrder();
    if (!order.length) return [];
    const i = Math.min(Math.max(this.lastIndex, 0), order.length - 1);
    const R = this.radius;
    const Rd = this.decodeRadius();
    const s0 = Math.min(Math.floor(pos / this.set.slice_s), this.set.slices - 1);
    const inSlice = pos - s0 * this.set.slice_s;
    const slices = [s0, s0 + 1];
    if (inSlice > this.set.slice_s / 2) slices.push(s0 + 2);
    const wants: Want[] = [];
    for (let j = Math.max(0, i - R); j <= Math.min(order.length - 1, i + R); j++) {
      const v = order[j]!;
      const dist = Math.abs(j - i);
      const dirMatch = j === i || Math.sign(j - i) === this.direction;
      const cost = dist * (dirMatch ? 1 : NET.directionPenalty) + 1;
      const fetchOnly = dist > Rd;
      slices.forEach((s, n) => {
        if (s >= this.set.slices && !loop) return; // no wrap-around prefetch unless looping
        const si = this.set.slices > 0 ? s % this.set.slices : s;
        wants.push({ key: { v, tier: 's', i: si }, priority: cost + n * 0.25, fetchOnly });
      });
    }
    const keep = new Set<string>();
    if (audible && this.set.listen.slices > 0) {
      const k = Math.min(Math.floor(pos / this.set.listen.slice_s), this.set.listen.slices - 1);
      const k1 = (k + 1) % this.set.listen.slices;
      const rh = this.set.variants[audible]?.render_hash;
      if (rh) {
        keep.add(listenUrl(this.set, rh, k));
        keep.add(listenUrl(this.set, rh, k1));
      }
      if (settled) {
        wants.push({ key: { v: audible, tier: 'l', i: k }, priority: PRIO.AUDIBLE_LISTEN });
        if (k1 !== k && (loop || k + 1 < this.set.listen.slices)) wants.push({ key: { v: audible, tier: 'l', i: k1 }, priority: PRIO.AUDIBLE_NEXT });
      }
    }
    // listen fetches for anything but the audible variant's current/next slice are wasted
    this.store.abortListen(keep);
    this.store.want(wants);
    return wants;
  }
}
