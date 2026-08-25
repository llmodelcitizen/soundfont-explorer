/**
 * Chains and the voice pool.
 *
 * A Chain plays one variant along the shared Timeline: a sequence of BufferSources, one per
 * segment, each with its own GainNode for the 5 ms seam ramps, all feeding the chain's voice
 * gain (used for the 10 ms switch crossfade) which feeds the master gain.
 *
 * Segments are addressed in UNWRAPPED song time u (see Timeline). For tier with slice length S:
 *   n = floor(u / D), x = u − n·D, i = floor(x / S)
 *   segment covers [n·D + i·S, n·D + min((i+1)·S, D))
 *   buffer offset for u = (x − i·S) + lead_in
 * The 20 ms bit-identical overlap between consecutive segments carries the seam crossfade.
 */
import { AUDIO } from '../config';
import type { SetDoc } from '../contracts/set';
import type { Timeline } from './clock';
import type { SegmentStore } from './store';
import type { BufferLike, ContextLike, GainLike, SegKey, SourceLike, Tier } from './types';
import { keyStr } from './types';

export interface Segment {
  tier: Tier;
  /** wrapped slice index (what the store knows) */
  i: number;
  /** unwrapped start / end */
  uStart: number;
  uEnd: number;
}

export function segmentAt(set: SetDoc, tier: Tier, u: number): Segment {
  const D = set.duration_s;
  const S = tier === 's' ? set.slice_s : set.listen.slice_s;
  const n = Math.floor(u / D);
  const x = u - n * D;
  const i = Math.min(Math.floor(x / S), (tier === 's' ? set.slices : set.listen.slices) - 1);
  return { tier, i, uStart: n * D + i * S, uEnd: n * D + Math.min((i + 1) * S, D) };
}

export interface Scheduled {
  seg: Segment;
  key: SegKey;
  src: SourceLike;
  g: GainLike;
  t0: number;
  t1: number;
}

export class Chain {
  readonly voice: GainLike;
  scheduled: Scheduled[] = [];
  /** unwrapped position up to which audio is scheduled */
  tail = 0;
  /** timeline generation this chain belongs to */
  gen: number;
  releasing = false;
  releasedAt = 0;
  /** tier of the most recently scheduled segment */
  lastTier: Tier = 's';
  /** set when fill() is waiting for a buffer */
  waitingFor: SegKey | null = null;
  lateStarts = 0;
  /** times fill() stopped because no segment could advance the tail (a set that does not cover
   *  its own duration): playback then simply runs out, so it is counted (here and on the store,
   *  where the debug panel reads it) and reported once */
  coverageStalls = 0;
  /** stretch already reported through store.lastError, so a stall does not rewrite it per tick */
  private stallReported: string | null = null;
  /** voice fade-in automation (for an analytic gain value when releasing mid-fade) */
  private fadeInT0 = 0;
  private fadeInDur = 0;

  constructor(
    readonly ctx: ContextLike,
    readonly variant: string,
    readonly set: SetDoc,
    readonly store: SegmentStore,
    readonly timeline: Timeline,
    master: GainLike,
    readonly preferTier: Tier = 'l',
  ) {
    this.voice = ctx.createGain();
    this.voice.gain.value = 0;
    this.voice.connect(master);
    this.gen = timeline.gen;
  }

  /**
   * Decoded buffer for the segment covering u, best tier first. With `advancing`, a tier whose
   * segment ends at or before u is passed over while another tier's does not: one tier's slices
   * can fall short of the duration while the other's cover it, and fill() must then be able to
   * use the covering tier instead of stopping on whichever one pick() happened to prefer.
   */
  pick(u: number, advancing = false): { seg: Segment; key: SegKey; buf: BufferLike } | null {
    const tiers: Tier[] = this.preferTier === 'l' ? ['l', 's'] : ['s', 'l'];
    let cands = tiers.map((tier) => {
      const seg = segmentAt(this.set, tier, u);
      return { seg, key: { v: this.variant, tier, i: seg.i } };
    });
    if (advancing) {
      const adv = cands.filter((x) => x.seg.uEnd > u);
      if (adv.length) cands = adv; // no tier advances: fall through, the caller reports the stall
    }
    // one counted lookup per pick: probing the preferred tier with has() keeps a scrub-tier hit
    // from also counting as a listen-tier miss (which pinned the debug panel's hit rate near 50 %)
    const c = cands.find((x) => this.store.has(x.key)) ?? cands[0]!;
    const buf = this.store.peek(c.key);
    return buf ? { seg: c.seg, key: c.key, buf } : null;
  }

  /**
   * Start the chain at unwrapped position u0 at context time t0. Returns false if no decoded
   * buffer covers u0 (the caller requests it and retries).
   */
  start(u0: number, t0: number, fadeIn: number): boolean {
    const p = this.pick(u0);
    if (!p) return false;
    this.scheduleSegment(p.seg, p.key, p.buf, u0, t0, /*rampIn*/ false);
    const g = this.voice.gain;
    g.cancelScheduledValues(t0);
    g.setValueAtTime(0, t0);
    g.linearRampToValueAtTime(1, t0 + fadeIn);
    this.fadeInT0 = t0;
    this.fadeInDur = fadeIn;
    return true;
  }

  /** voice gain the automation will have reached at context time t (before any release) */
  voiceGainAt(t: number): number {
    if (this.fadeInDur <= 0) return 1;
    return Math.min(1, Math.max(0, (t - this.fadeInT0) / this.fadeInDur));
  }

  /**
   * The timeline rebased its origin by `seconds` (whole loops): shift every unwrapped
   * coordinate we hold so fill() keeps measuring against the same absolute times.
   */
  shift(seconds: number): void {
    this.tail += seconds;
    for (const s of this.scheduled) {
      s.seg = { ...s.seg, uStart: s.seg.uStart + seconds, uEnd: s.seg.uEnd + seconds };
    }
  }

  /** chains built for an older timeline generation must not schedule anything more */
  get stale(): boolean {
    return this.gen !== this.timeline.gen;
  }

  /** Extend scheduling to cover [now, now + LOOKAHEAD]. Returns the key it is waiting for, if any. */
  fill(now: number = this.ctx.currentTime): SegKey | null {
    if (this.releasing || this.stale) return null;
    const target = this.timeline.unwrapped(now) + AUDIO.LOOKAHEAD;
    while (this.tail < target) {
      if (!this.timeline.loop && this.tail >= this.set.duration_s) break;
      const p = this.pick(this.tail, /* advancing */ true);
      if (!p) {
        // nothing decoded for this boundary yet: ask for the scrub tier (the cheapest round
        // trip), unless its slices no longer reach this stretch and the preferred tier's do
        const s = segmentAt(this.set, 's', this.tail);
        const want = s.uEnd > this.tail ? s : segmentAt(this.set, this.preferTier, this.tail);
        this.waitingFor = { v: this.variant, tier: want.tier, i: want.i };
        return this.waitingFor;
      }
      // a set whose slices do not cover its duration (or a zero slice length) yields a last
      // segment ending at or before the tail — on every tier, pick() having already preferred
      // one that advances. Scheduling it could never advance, so stop here instead of spinning
      // forever (the `!(a > b)` form also catches NaN). Breaking out looks to the engine exactly
      // like "fully scheduled", i.e. audio just runs out at the tail with no status of its own,
      // so record it where the debug panel already reads (store.lastError) rather than letting a
      // malformed set fail silently — once per stretch: fill() runs on every engine tick and
      // every frame, and rewriting lastError each time would bury every other error the panel
      // could show for as long as the song plays.
      if (!(p.seg.uEnd > this.tail)) {
        this.coverageStalls++;
        this.store.stats.coverageStalls++;
        const msg = `${keyStr(p.key)}: segment ends at ${p.seg.uEnd} ≤ tail ${this.tail} — the set's slices do not cover its duration`;
        if (msg !== this.stallReported) {
          this.stallReported = msg;
          this.store.lastError = msg;
        }
        break;
      }
      const tSeam = this.timeline.timeAt(this.tail);
      this.scheduleSegment(p.seg, p.key, p.buf, this.tail, tSeam, /*rampIn*/ true);
    }
    this.waitingFor = null;
    return null;
  }

  private scheduleSegment(seg: Segment, key: SegKey, buf: BufferLike, u: number, t: number, rampIn: boolean): void {
    const half = AUDIO.SEAM_XFADE / 2;
    const leadIn = this.set.lead_in_s;
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const g = ctx.createGain();
    src.connect(g);
    g.connect(this.voice);
    // where in the buffer does unwrapped position u live?
    const inSeg = u - seg.uStart; // ≥ 0 for a fresh start, or −half at a seam
    const startU = rampIn ? u - half : u;
    const startT = rampIn ? t - half : t;
    const offset = Math.max(0, inSeg - (rampIn ? half : 0) + leadIn);
    const endU = seg.uEnd + half;
    const endT = t + (endU - u);
    // gain envelope: (ramp in over the seam) … hold … ramp out over the next seam
    g.gain.cancelScheduledValues(startT);
    if (rampIn) {
      g.gain.setValueAtTime(0, startT);
      g.gain.linearRampToValueAtTime(1, startT + AUDIO.SEAM_XFADE);
    } else {
      g.gain.setValueAtTime(1, startT);
    }
    const outStart = endT - AUDIO.SEAM_XFADE;
    g.gain.setValueAtTime(1, Math.max(outStart, startT + (rampIn ? AUDIO.SEAM_XFADE : 0)));
    g.gain.linearRampToValueAtTime(0, endT);
    // if we are late (main thread hiccup), start now at the matching offset so alignment holds
    const late = Math.max(0, ctx.currentTime - startT);
    const realStart = startT + late;
    const realOffset = offset + late;
    const dur = Math.min(endT - realStart, buf.duration - realOffset);
    if (dur <= 0) {
      try { src.disconnect(); g.disconnect(); } catch { /* ignore */ }
      this.tail = seg.uEnd;
      return;
    }
    src.start(realStart, realOffset, dur);
    if (late > 0) this.lateStarts++;
    this.store.pin(key);
    const sch: Scheduled = { seg, key, src, g, t0: realStart, t1: realStart + dur };
    this.scheduled.push(sch);
    this.tail = seg.uEnd;
    this.lastTier = seg.tier;
    src.onended = () => this.dispose(sch);
    this.gc(ctx.currentTime);
  }

  private dispose(s: Scheduled): void {
    const idx = this.scheduled.indexOf(s);
    if (idx < 0) return;
    this.scheduled.splice(idx, 1);
    try {
      s.src.disconnect();
      s.g.disconnect();
    } catch {
      /* already gone */
    }
    this.store.unpin(s.key);
  }

  private gc(now: number): void {
    for (const s of [...this.scheduled]) if (s.t1 < now - 1) this.dispose(s);
  }

  /** Segment audible at context time t (or null). */
  segmentAtTime(t: number): Scheduled | null {
    let best: Scheduled | null = null;
    for (const s of this.scheduled) if (s.t0 <= t && t < s.t1 && (!best || s.t0 > best.t0)) best = s;
    return best;
  }

  currentTier(t: number): Tier | null {
    return this.segmentAtTime(t)?.seg.tier ?? null;
  }

  /** Ramp the voice to silence over `fade` starting at t and stop everything after. */
  release(t: number, fade: number): void {
    if (this.releasing) return;
    this.releasing = true;
    this.releasedAt = t + fade;
    const g = this.voice.gain;
    // hold the value the automation will have reached at t (not the current value: t is in the
    // future and the fade-in may still be in progress), then ramp to silence
    g.cancelScheduledValues(t);
    g.setValueAtTime(this.voiceGainAt(t), t);
    g.linearRampToValueAtTime(0, t + fade);
    for (const s of this.scheduled) {
      try {
        s.src.stop(t + fade + 0.001);
      } catch {
        /* not started / already stopped */
      }
    }
  }

  /** Immediate teardown (used after release completes or on stale gen). */
  destroy(): void {
    this.releasing = true;
    for (const s of [...this.scheduled]) {
      try {
        s.src.stop();
      } catch {
        /* ignore */
      }
      this.dispose(s);
    }
    try {
      this.voice.disconnect();
    } catch {
      /* ignore */
    }
  }

  get keys(): string[] {
    return this.scheduled.map((s) => keyStr(s.key));
  }
}

/** Bounded set of chains: the audible one is never stolen; the oldest releasing one is. */
export class VoicePool {
  chains: Chain[] = [];
  stolen = 0;

  constructor(private readonly max = AUDIO.VOICES) {}

  add(c: Chain, now: number): void {
    this.reap(now);
    if (this.chains.length >= this.max) {
      const victim = this.chains
        .filter((x) => x.releasing)
        .sort((a, b) => a.releasedAt - b.releasedAt)[0];
      if (victim) {
        victim.destroy();
        this.chains = this.chains.filter((x) => x !== victim);
        this.stolen++;
      }
    }
    this.chains.push(c);
  }

  /** Destroy chains whose release has completed. */
  reap(now: number): void {
    const keep: Chain[] = [];
    for (const c of this.chains) {
      if (c.releasing && now > c.releasedAt + 0.05) c.destroy();
      else keep.push(c);
    }
    this.chains = keep;
  }

  clear(): void {
    for (const c of this.chains) c.destroy();
    this.chains = [];
  }

  get size(): number {
    return this.chains.length;
  }
}
