/**
 * Engine facade: one AudioContext, one Timeline, a VoicePool of Chains, the SegmentStore,
 * the Prefetcher and the switch logic (plan §10 "switcher.ts" lives here as a class).
 *
 * Invariants carried over from the MVP:
 *  - the audible chain is never released before its successor is scheduled
 *  - commit only when the target's buffer is decoded; on timeout keep playing the old one —
 *    and when there is no old one to keep (play from pause/ended, seek), hold the playhead
 *    (buffering) until the target decodes instead of letting it run on in silence
 *  - last select() wins (seq token); superseded selects are dropped, never queued
 */
import { AUDIO, POLICY } from '../config';
import type { SetDoc } from '../contracts/set';
import { Timeline } from './clock';
import { PRIO, Prefetcher } from './prefetcher';
import { Chain, VoicePool, segmentAt } from './scheduler';
import type { SegmentStore } from './store';
import type { ContextLike, GainLike, SegKey, Tier } from './types';
import { keyStr } from './types';

export type StatusKind = 'idle' | 'loading' | 'playing' | 'paused' | 'stopped' | 'wontload' | 'ended';

export interface Status {
  kind: StatusKind;
  message: string;
  /** variant we are trying to reach, if loading */
  target?: string | null;
}

export interface Metrics {
  switchLatencyMs: number[];
  lateStarts: number;
  voices: number;
  stolen: number;
  decodedBytes: number;
  compressedBytes: number;
  cacheHitRate: number;
  decodeKind: string;
  decodeAvgMs: number;
  fetchAvgMs: number;
  fetchBytes: number;
  fetchErrors: number;
  inflight: number;
  queued: number;
  radius: number;
  velocity: number;
  baseLatency: number;
  outputLatency: number;
  sampleRate: number;
}

export interface EngineEvents {
  status: Status;
  audible: string | null;
  tier: Tier | null;
}

type Listener<K extends keyof EngineEvents> = (v: EngineEvents[K]) => void;

interface Pending {
  token: number;
  variant: string;
  sinceMs: number;
  selectedAtMs: number;
  fade: number;
}

export class Engine {
  readonly timeline: Timeline;
  readonly pool = new VoicePool(AUDIO.VOICES);
  readonly prefetcher: Prefetcher;
  readonly master: GainLike;
  audible: string | null = null;
  audibleChain: Chain | null = null;
  status: Status = { kind: 'idle', message: '' };
  private seq = 0;
  private pending: Pending | null = null;
  private lastSwitchMs = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private tickN = 0;
  private listeners: { [K in keyof EngineEvents]: Set<Listener<K>> } = { status: new Set(), audible: new Set(), tier: new Set() };
  private lastTier: Tier | null = null;
  /** pins held for the previous variant's buffers (quick A/B); at most one chain's worth */
  private hysteresis: { keys: SegKey[]; untilMs: number } | null = null;
  private unsubDecoded: () => void;
  private volume = 1;
  private muted = false;
  /** true after the song ran to its end by itself (not a user pause): the next select() restarts it */
  private endedNaturally = false;
  /**
   * Buffering: play was asked for, but nothing is decoded at the playhead and nothing else is
   * sounding, so the timeline is held (paused) there until the pending target commits.
   * `playing` stays true — it reports the user's intent, not the timeline's state.
   */
  private buffering = false;
  readonly metrics = { switchLatencyMs: [] as number[] };
  private order: string[] = [];
  private cursorIndex = 0;

  get currentTier(): Tier | null {
    return this.lastTier;
  }

  constructor(
    readonly ctx: ContextLike,
    readonly set: SetDoc,
    readonly store: SegmentStore,
    private readonly nowMs: () => number = () => (typeof performance !== 'undefined' ? performance.now() : Date.now()),
  ) {
    this.timeline = new Timeline(() => ctx.currentTime, set.duration_s);
    this.master = ctx.createGain();
    this.master.gain.value = 1;
    this.master.connect(ctx.destination);
    this.prefetcher = new Prefetcher(store, set, () => this.order);
    this.unsubDecoded = store.onDecoded((key) => this.onDecoded(key));
  }

  // ---- events -------------------------------------------------------------

  on<K extends keyof EngineEvents>(k: K, fn: Listener<K>): () => void {
    this.listeners[k].add(fn);
    return () => this.listeners[k].delete(fn);
  }

  private emit<K extends keyof EngineEvents>(k: K, v: EngineEvents[K]): void {
    for (const fn of this.listeners[k]) fn(v);
  }

  private setStatus(kind: StatusKind, message = '', target: string | null = null): void {
    if (this.status.kind === kind && this.status.message === message && this.status.target === target) return;
    this.status = { kind, message, target };
    this.emit('status', this.status);
  }

  // ---- lifecycle ----------------------------------------------------------

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => this.tick(), AUDIO.RETRY_TICK_MS);
  }

  dispose(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.unsubDecoded();
    this.store.abortAll();
    this.pool.clear();
    try {
      this.master.disconnect();
    } catch {
      /* ignore */
    }
  }

  // ---- order / cursor (for prefetch) --------------------------------------

  setOrder(order: string[], cursorIndex: number): void {
    this.order = order;
    this.cursorIndex = cursorIndex;
    this.prefetcher.cursor(cursorIndex, this.nowMs(), /*jump*/ true);
  }

  cursor(index: number): void {
    this.cursorIndex = index;
    this.prefetcher.cursor(index, this.nowMs());
  }

  // ---- transport ----------------------------------------------------------

  position(): number {
    return this.timeline.position();
  }

  get playing(): boolean {
    return this.timeline.playing || this.buffering;
  }

  play(): void {
    if (this.playing) return;
    this.endedNaturally = false;
    this.timeline.play(); // from the end this restarts at 0
    this.restartAudible(AUDIO.SEAM_XFADE);
  }

  /** explicit user pause: selections made while paused stay silent until play() */
  pause(): void {
    if (!this.playing) return;
    this.endedNaturally = false;
    const now = this.ctx.currentTime;
    this.timeline.pause(now); // no-op while buffering (already held)
    this.buffering = false;
    this.audibleChain?.release(now, AUDIO.SWITCH_XFADE);
    this.audibleChain = null;
    if (this.pending) {
      // the user asked for this variant; play() will resume on it
      this.setAudible(this.pending.variant, null);
      this.pending = null;
    }
    this.setStatus('paused');
  }

  /** explicit user stop: silence the current voice and reset the next play to position zero */
  stop(): void {
    this.endedNaturally = false;
    const now = this.ctx.currentTime;
    this.timeline.pause(now);
    this.timeline.seek(0, now);
    this.buffering = false;
    this.audibleChain?.release(now, AUDIO.SWITCH_XFADE);
    this.audibleChain = null;
    if (this.pending) {
      // Preserve the requested variant, just as pause() does, but rewind its next start.
      this.setAudible(this.pending.variant, null);
      this.pending = null;
    }
    this.setStatus('stopped');
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(pos: number): void {
    const now = this.ctx.currentTime;
    this.timeline.seek(pos, now);
    if (this.playing) this.restartAudible(AUDIO.SEAM_XFADE);
  }

  setLoop(on: boolean): void {
    this.timeline.loop = on;
    // a chain that stopped at the end needs to continue; fill() handles it on the next tick
  }

  setVolume(v: number): void {
    this.volume = Math.min(1, Math.max(0, v));
    this.applyGain();
  }

  setMuted(m: boolean): void {
    this.muted = m;
    this.applyGain();
  }

  get isMuted(): boolean {
    return this.muted;
  }

  private applyGain(): void {
    const g = this.master.gain;
    const t = this.ctx.currentTime;
    const target = this.muted ? 0 : this.volume * this.volume; // perceptual-ish curve
    g.cancelScheduledValues(t);
    g.setValueAtTime(g.value, t);
    g.linearRampToValueAtTime(target, t + 0.01);
  }

  // ---- selection / switching ----------------------------------------------

  /** Ask to hear `variant`. `selectedAtMs` = the originating input timestamp for latency metrics. */
  select(variant: string, selectedAtMs: number = this.nowMs()): void {
    if (!Object.hasOwn(this.set.variants, variant)) return;
    const token = ++this.seq;
    if (variant === this.audible && !this.pending && this.audibleChain && !this.audibleChain.releasing && this.timeline.playing) return;
    // re-selecting the target we are already waiting for must not restart its timeout
    const sinceMs = this.pending?.variant === variant ? this.pending.sinceMs : this.nowMs();
    this.pending = { token, variant, sinceMs, selectedAtMs, fade: AUDIO.SWITCH_XFADE };
    if (!this.timeline.playing && this.endedNaturally) {
      // the song finished on its own: a new choice means "hear this one" → start over from 0
      this.endedNaturally = false;
      this.timeline.play();
      this.setStatus('playing');
    }
    this.tryCommit();
  }

  /** Rebuild the audible chain at the current position (play/seek). */
  private restartAudible(fade: number): void {
    // a pending target survives seek/play: the user asked for it, they just moved the playhead
    const v = this.pending?.variant ?? this.audible ?? this.order[this.cursorIndex] ?? this.set.order[0];
    if (!v) return;
    const token = ++this.seq;
    this.pending = { token, variant: v, sinceMs: this.nowMs(), selectedAtMs: this.nowMs(), fade };
    this.tryCommit();
  }

  private startLead(): number {
    return AUDIO.startLead(this.ctx.baseLatency || 0);
  }

  private tryCommit(): void {
    const p = this.pending;
    if (!p || p.token !== this.seq) return;
    if (!this.playing) {
      // paused: just redesignate; chains are rebuilt on play()
      this.setAudible(p.variant, null);
      this.pending = null;
      return;
    }
    const now = this.ctx.currentTime;
    const lead = Math.max(AUDIO.COMMIT_LEAD, this.startLead());
    const t0 = now + lead;
    const u0 = this.timeline.unwrapped(t0); // while held: the held position
    if (!this.timeline.loop && u0 >= this.set.duration_s) {
      // past the end: nothing to schedule; tick() reports the end (and lifts a hold)
      this.pending = null;
      return;
    }
    if (this.buffering && this.decodedAt(p.variant, u0)) {
      // held here waiting for exactly this: let the timeline run again so u0 plays at t0 —
      // before the chain is built, since a chain binds to the timeline generation
      this.timeline.play(t0);
      this.buffering = false;
    }
    const old = this.audibleChain;
    const chain = new Chain(this.ctx, p.variant, this.set, this.store, this.timeline, this.master, 'l');
    if (!chain.start(u0, t0, p.fade)) {
      chain.destroy();
      const seg = segmentAt(this.set, 's', u0);
      this.store.request({ v: p.variant, tier: 's', i: seg.i }, PRIO.URGENT).catch(() => undefined);
      if (!this.hasFallback()) {
        // nothing sounds (play from pause/ended, or the chain a seek left stale): hold the playhead
        // here until the target decodes. Letting it run on would chase a moving position, and a
        // dropped target would leave nothing to re-arm the commit when the bytes finally land.
        if (this.timeline.playing) this.timeline.pause(now);
        this.buffering = true;
        this.setStatus('loading', 'buffering', p.variant);
        return;
      }
      const waited = this.nowMs() - p.sinceMs;
      if (waited > AUDIO.SWITCH_TIMEOUT_MS) {
        this.pending = null;
        this.setStatus('wontload', `won't load — still on ${this.audible ?? '—'}`, p.variant);
        return;
      }
      this.setStatus('loading', 'loading', p.variant);
      return;
    }
    // committed: new chain scheduled; now (and only now) release the old one
    this.pool.add(chain, now);
    if (old && old !== chain) {
      old.release(t0, p.fade);
      this.armHysteresis(old);
    }
    this.audibleChain = chain;
    this.pending = null;
    this.lastSwitchMs = this.nowMs();
    this.metrics.switchLatencyMs.push(this.nowMs() - p.selectedAtMs);
    if (this.metrics.switchLatencyMs.length > 500) this.metrics.switchLatencyMs.shift();
    this.setAudible(p.variant, chain.currentTier(t0 + 0.001));
    this.setStatus('playing');
    chain.fill(now);
  }

  /** a chain that is (and will keep) sounding at the current position if the target never loads */
  private hasFallback(): boolean {
    const c = this.audibleChain;
    return !!c && !c.stale && !c.releasing;
  }

  /** a decoded buffer of either tier covers unwrapped position u for `variant` (what Chain.start() needs) */
  private decodedAt(variant: string, u: number): boolean {
    return (['l', 's'] as const).some((tier) => this.store.decoded.has(keyStr({ v: variant, tier, i: segmentAt(this.set, tier, u).i })));
  }

  private setAudible(v: string, tier: Tier | null): void {
    if (this.audible !== v) {
      this.audible = v;
      this.emit('audible', v);
    }
    if (tier !== this.lastTier) {
      this.lastTier = tier;
      this.emit('tier', tier);
    }
  }

  /**
   * Keep the previous variant's current buffers pinned for hysteresisMs (quick A/B).
   * Only the most recent previous chain is held: a fast scrub run must not accumulate pins.
   */
  private armHysteresis(old: Chain): void {
    this.dropHysteresis();
    const keys = old.scheduled.map((s) => s.key);
    for (const k of keys) this.store.pin(k);
    this.hysteresis = { keys, untilMs: this.nowMs() + POLICY.hysteresisMs };
  }

  private dropHysteresis(): void {
    if (this.hysteresis) for (const k of this.hysteresis.keys) this.store.unpin(k);
    this.hysteresis = null;
  }

  // ---- tick ---------------------------------------------------------------

  private onDecoded(key: SegKey): void {
    if (this.pending && key.v === this.pending.variant) this.tryCommit();
    // waitingFor always belongs to the chain's own variant, so any decode for it may unblock fill()
    const c = this.audibleChain;
    if (c?.waitingFor && key.v === c.variant) this.fillAudible();
  }

  private fillAudible(): void {
    const c = this.audibleChain;
    if (!c) return;
    const missing = c.fill(this.ctx.currentTime);
    if (missing) {
      this.store.request(missing, PRIO.URGENT).catch(() => undefined);
      // also the listen slice for that boundary (quality), lower priority
      const seg = segmentAt(this.set, 'l', c.tail);
      this.store.request({ v: c.variant, tier: 'l', i: seg.i }, PRIO.AUDIBLE_NEXT).catch(() => undefined);
    }
  }

  tick(): void {
    const now = this.ctx.currentTime;
    const nowMs = this.nowMs();
    this.tickN++;
    this.pool.reap(now);
    if (this.hysteresis && this.hysteresis.untilMs <= nowMs) this.dropHysteresis();
    if (this.playing) {
      if (this.timeline.loop) {
        const n = this.timeline.rebase(now); // 0 while held
        if (n > 0) for (const c of this.pool.chains) c.shift(-n * this.set.duration_s);
      } else if (this.timeline.ended(now) || (this.buffering && this.position() >= this.set.duration_s)) {
        this.timeline.pause(now);
        this.buffering = false;
        this.audibleChain?.release(now, AUDIO.SWITCH_XFADE);
        this.audibleChain = null;
        this.endedNaturally = true;
        this.setStatus('ended');
        return;
      }
    }
    if (this.pending) this.tryCommit();
    const c = this.audibleChain;
    if (c && this.timeline.playing) {
      this.fillAudible();
      const tier = c.currentTier(now);
      if (tier !== this.lastTier && tier) this.setAudible(c.variant, tier);
      this.maybeUpgradeTier(now, nowMs, c);
    }
    if (this.tickN % 4 === 0) {
      this.prefetcher.decay(nowMs);
      const settled = !this.pending && nowMs - this.lastSwitchMs >= POLICY.settleMs;
      this.prefetcher.tick(this.position(), this.audibleChain?.variant ?? this.audible, settled, this.timeline.loop);
    }
  }

  /** Settled ≥ settleMs on the scrub tier → request the listen slice and crossfade to it once decoded. */
  private maybeUpgradeTier(now: number, nowMs: number, c: Chain): void {
    if (this.pending || nowMs - this.lastSwitchMs < POLICY.settleMs) return;
    if (c.currentTier(now) !== 's') return;
    const lead = this.startLead();
    const t0 = now + lead;
    const u0 = this.timeline.unwrapped(t0);
    const seg = segmentAt(this.set, 'l', u0);
    const key = { v: c.variant, tier: 'l' as const, i: seg.i };
    if (!this.store.peek(key)) {
      this.store.request(key, PRIO.AUDIBLE_LISTEN).catch(() => undefined);
      return;
    }
    const chain = new Chain(this.ctx, c.variant, this.set, this.store, this.timeline, this.master, 'l');
    if (!chain.start(u0, t0, AUDIO.TIER_XFADE)) {
      chain.destroy();
      return;
    }
    this.pool.add(chain, now);
    c.release(t0, AUDIO.TIER_XFADE);
    this.audibleChain = chain;
    chain.fill(now);
    this.setAudible(c.variant, 'l');
  }

  // ---- metrics ------------------------------------------------------------

  /** RMS of the buffer currently audible (diagnostic: distinguishes "decoded silence" from "muted output") */
  audibleRms(): string {
    const c = this.audibleChain;
    const s = c?.segmentAtTime(this.ctx.currentTime);
    const buf = s?.src.buffer as unknown as { getChannelData?: (i: number) => Float32Array; length: number } | undefined;
    if (!buf?.getChannelData) return 'n/a';
    const ch = buf.getChannelData(0);
    let sum = 0;
    const step = Math.max(1, Math.floor(ch.length / 4000));
    let n = 0;
    for (let i = 0; i < ch.length; i += step) {
      sum += ch[i]! * ch[i]!;
      n++;
    }
    return `${Math.sqrt(sum / Math.max(1, n)).toFixed(4)} (${keyStr(s!.key)})`;
  }

  snapshot(extra: { decodeKind: string; decodeAvgMs: number; fetchAvgMs: number; fetchBytes: number; fetchErrors: number; inflight: number; queued: number }): Metrics {
    const d = this.store.decoded;
    const total = d.hits + d.misses;
    return {
      switchLatencyMs: [...this.metrics.switchLatencyMs],
      lateStarts: this.pool.chains.reduce((n, c) => n + c.lateStarts, 0),
      voices: this.pool.size,
      stolen: this.pool.stolen,
      decodedBytes: d.bytes,
      compressedBytes: this.store.compressed.bytes,
      cacheHitRate: total ? d.hits / total : 0,
      radius: this.prefetcher.radius,
      velocity: this.prefetcher.velocity,
      baseLatency: this.ctx.baseLatency,
      outputLatency: this.ctx.outputLatency ?? 0,
      sampleRate: this.ctx.sampleRate,
      ...extra,
    };
  }
}
