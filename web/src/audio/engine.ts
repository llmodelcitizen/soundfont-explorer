/**
 * Engine facade: one AudioContext, one Timeline, a VoicePool of Chains, the SegmentStore,
 * the Prefetcher and the switch logic (plan §10 "switcher.ts" lives here as a class).
 *
 * Invariants carried over from the MVP:
 *  - the audible chain is never released before its successor is scheduled
 *  - commit only when the target's buffer is decoded; on timeout keep playing the old one
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

export type StatusKind = 'idle' | 'loading' | 'playing' | 'paused' | 'wontload' | 'ended';

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
  private hysteresis: { keys: SegKey[]; untilMs: number }[] = [];
  private unsubDecoded: () => void;
  private volume = 1;
  private muted = false;
  readonly metrics = { switchLatencyMs: [] as number[] };
  private order: string[] = [];
  private cursorIndex = 0;

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
    return this.timeline.playing;
  }

  play(): void {
    if (this.timeline.playing) return;
    this.timeline.play();
    this.restartAudible(AUDIO.SEAM_XFADE);
  }

  pause(): void {
    if (!this.timeline.playing) return;
    const now = this.ctx.currentTime;
    this.timeline.pause(now);
    this.audibleChain?.release(now, AUDIO.SWITCH_XFADE);
    this.audibleChain = null;
    if (this.pending) {
      // the user asked for this variant; play() will resume on it
      this.setAudible(this.pending.variant, null);
      this.pending = null;
    }
    this.setStatus('paused');
  }

  toggle(): void {
    if (this.timeline.playing) this.pause();
    else this.play();
  }

  seek(pos: number): void {
    const now = this.ctx.currentTime;
    this.timeline.seek(pos, now);
    if (this.timeline.playing) this.restartAudible(AUDIO.SEAM_XFADE);
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
    if (variant === this.audible && !this.pending && this.audibleChain && !this.audibleChain.releasing) return;
    this.pending = { token, variant, sinceMs: this.nowMs(), selectedAtMs, fade: AUDIO.SWITCH_XFADE };
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
    if (!this.timeline.playing) {
      // paused: just redesignate; chains are rebuilt on play()
      this.setAudible(p.variant, null);
      this.pending = null;
      return;
    }
    const now = this.ctx.currentTime;
    const lead = Math.max(AUDIO.COMMIT_LEAD, this.startLead());
    const t0 = now + lead;
    const u0 = this.timeline.unwrapped(t0);
    if (!this.timeline.loop && u0 >= this.set.duration_s) {
      this.pending = null;
      return;
    }
    const old = this.audibleChain;
    const chain = new Chain(this.ctx, p.variant, this.set, this.store, this.timeline, this.master, 'l');
    if (!chain.start(u0, t0, p.fade)) {
      chain.destroy();
      const seg = segmentAt(this.set, 's', u0);
      this.store.request({ v: p.variant, tier: 's', i: seg.i }, PRIO.URGENT).catch(() => undefined);
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
    this.hysteresis.push({ keys, untilMs: this.nowMs() + POLICY.hysteresisMs });
  }

  private dropHysteresis(): void {
    for (const h of this.hysteresis) for (const k of h.keys) this.store.unpin(k);
    this.hysteresis = [];
  }

  // ---- tick ---------------------------------------------------------------

  private onDecoded(key: SegKey): void {
    if (this.pending && key.v === this.pending.variant) this.tryCommit();
    const c = this.audibleChain;
    if (c && c.waitingFor && keyStr(c.waitingFor) === keyStr(key)) this.fillAudible();
    else if (c && key.v === c.variant && c.waitingFor) this.fillAudible();
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
    // hysteresis pins expire
    this.hysteresis = this.hysteresis.filter((h) => {
      if (h.untilMs > nowMs) return true;
      for (const k of h.keys) this.store.unpin(k);
      return false;
    });
    if (this.timeline.playing) {
      if (this.timeline.loop) {
        const n = this.timeline.rebase(now);
        if (n > 0) for (const c of this.pool.chains) c.shift(-n * this.set.duration_s);
      } else if (this.timeline.ended(now)) {
        this.timeline.pause(now);
        this.audibleChain?.release(now, AUDIO.SWITCH_XFADE);
        this.audibleChain = null;
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
    return `${Math.sqrt(sum / Math.max(1, n)).toFixed(4)} (${s!.key.v}/${s!.key.tier}/${s!.key.i})`;
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
