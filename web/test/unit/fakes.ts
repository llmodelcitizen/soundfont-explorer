/**
 * Fakes for engine tests: a clockable AudioContext, an in-memory fetch serving SFPK packs and
 * listen objects with controllable latency, and a decoder that turns descriptor bytes into
 * fake buffers. Time is advanced manually (vi.useFakeTimers for timers, ctx.advance for audio).
 */
import { listenUrl, packUrl, type SetDoc } from '../../src/contracts/set';
import type { Decoder } from '../../src/audio/decode';
import { DecodeQueue } from '../../src/audio/decode';
import type { FetchFn } from '../../src/audio/net/fetcher';
import type { BufferLike, ContextLike, GainLike, NodeLike, ParamLike, SourceLike } from '../../src/audio/types';

// ---------------------------------------------------------------- audio

export interface AutomationEvent {
  kind: 'set' | 'ramp' | 'cancel';
  value?: number;
  t: number;
}

export class FakeParam implements ParamLike {
  events: AutomationEvent[] = [];
  constructor(public value: number) {}
  cancelScheduledValues(t: number) {
    this.events.push({ kind: 'cancel', t });
  }
  setValueAtTime(v: number, t: number) {
    this.events.push({ kind: 'set', value: v, t });
  }
  linearRampToValueAtTime(v: number, t: number) {
    this.events.push({ kind: 'ramp', value: v, t });
  }
  /** piecewise-linear evaluation of the automation at time t (ignoring cancel semantics beyond truncation) */
  valueAt(t: number): number {
    let v = this.value;
    let lastT = -Infinity;
    let lastV = this.value;
    for (const e of this.events) {
      if (e.kind === 'cancel') {
        continue;
      }
      if (e.t > t) {
        if (e.kind === 'ramp' && lastT <= t) {
          const f = e.t === lastT ? 1 : (t - lastT) / (e.t - lastT);
          return lastV + (e.value! - lastV) * Math.min(1, Math.max(0, f));
        }
        return lastV;
      }
      lastT = e.t;
      lastV = e.value!;
      v = lastV;
    }
    return v;
  }
}

export class FakeNode implements NodeLike {
  connected: unknown[] = [];
  disconnected = false;
  connect(dst: unknown) {
    this.connected.push(dst);
  }
  disconnect() {
    this.disconnected = true;
  }
}

export class FakeGain extends FakeNode implements GainLike {
  gain = new FakeParam(1);
}

export class FakeBuffer implements BufferLike {
  constructor(public duration: number, public sampleRate = 48000, public numberOfChannels = 2, public tag = '') {}
  get length() {
    return Math.round(this.duration * this.sampleRate);
  }
  copyToChannel() {}
}

export class FakeSource extends FakeNode implements SourceLike {
  buffer: BufferLike | null = null;
  onended: ((ev?: unknown) => void) | null = null;
  started: { when: number; offset: number; duration?: number } | null = null;
  stopped: number | null = null;
  constructor(private ctx: FakeAudioContext) {
    super();
  }
  start(when = 0, offset = 0, duration?: number) {
    if (this.started) throw new Error('InvalidStateError: start called twice');
    this.started = { when: Math.max(when, this.ctx.currentTime), offset, duration };
    this.ctx.sources.push(this);
  }
  stop(when = 0) {
    if (!this.started) throw new Error('InvalidStateError: stop before start');
    this.stopped = Math.max(when, this.ctx.currentTime);
  }
  get endTime(): number {
    if (!this.started) return Infinity;
    const natural = this.started.duration != null ? this.started.when + this.started.duration : this.started.when + (this.buffer?.duration ?? 0) - this.started.offset;
    return Math.min(natural, this.stopped ?? Infinity);
  }
  /** the song-time this source claims to play at context time t, if playing */
  playingAt(t: number): boolean {
    return !!this.started && this.started.when <= t && t < this.endTime;
  }
}

export class FakeAudioContext implements ContextLike {
  currentTime = 0;
  sampleRate = 48000;
  baseLatency = 0.005;
  outputLatency = 0.02;
  state = 'running';
  destination = new FakeNode();
  sources: FakeSource[] = [];
  gains: FakeGain[] = [];
  decodeCalls = 0;
  createGain() {
    const g = new FakeGain();
    this.gains.push(g);
    return g;
  }
  createBufferSource() {
    return new FakeSource(this);
  }
  createBuffer(channels: number, length: number, sampleRate: number) {
    return new FakeBuffer(length / sampleRate, sampleRate, channels);
  }
  async decodeAudioData(data: ArrayBuffer): Promise<BufferLike> {
    this.decodeCalls++;
    return descriptorToBuffer(data);
  }
  /** advance audio time, firing onended for sources that finished */
  advance(dt: number) {
    this.currentTime += dt;
    for (const s of this.sources) {
      if (s.started && !(s as unknown as { _ended?: boolean })._ended && s.endTime <= this.currentTime) {
        (s as unknown as { _ended?: boolean })._ended = true;
        s.onended?.();
      }
    }
  }
  /** sources audible at the current time */
  live(): FakeSource[] {
    return this.sources.filter((s) => s.playingAt(this.currentTime));
  }
}

// ---------------------------------------------------------------- data

export interface Descriptor {
  v: string;
  tier: 's' | 'l';
  i: number;
}

const enc = new TextEncoder();
const dec = new TextDecoder();

export function descriptorBytes(d: Descriptor, pad = 64): ArrayBuffer {
  const s = JSON.stringify(d);
  const out = new Uint8Array(Math.max(pad, s.length));
  out.set(enc.encode(s));
  return out.buffer;
}

export function descriptorToBuffer(bytes: ArrayBuffer): FakeBuffer {
  const txt = dec.decode(new Uint8Array(bytes)).replace(/\0+$/, '');
  const d = JSON.parse(txt) as Descriptor;
  return new FakeBuffer(d.tier === 's' ? 2.14 : 10.14, 48000, 2, `${d.v}/${d.tier}/${d.i}`);
}

export function packBytes(members: ArrayBuffer[]): ArrayBuffer {
  const n = members.length;
  const head = new Uint8Array(8 + 4 * n);
  head.set([0x53, 0x46, 0x50, 0x4b, 1, n, 0, 0]);
  const dv = new DataView(head.buffer);
  members.forEach((m, j) => dv.setUint32(8 + 4 * j, m.byteLength, true));
  const total = head.length + members.reduce((a, m) => a + m.byteLength, 0);
  const out = new Uint8Array(total);
  out.set(head);
  let off = head.length;
  for (const m of members) {
    out.set(new Uint8Array(m), off);
    off += m.byteLength;
  }
  return out.buffer;
}

/** Build a synthetic set: `variants` ids, duration D (multiple of 2), packs of `packSize`. */
export function makeSet(variants: string[], D = 8, packSize = 24): { set: SetDoc; objects: Map<string, ArrayBuffer> } {
  const slices = D / 2;
  const listenSlices = Math.ceil(D / 10);
  const groups: SetDoc['groups'] = [];
  const vmap: SetDoc['variants'] = {};
  for (let g = 0; g * packSize < variants.length; g++) {
    const ids = variants.slice(g * packSize, (g + 1) * packSize);
    groups.push({ hash: `g${g}`, variants: ids });
    ids.forEach((id, slot) => {
      vmap[id] = { render_hash: `rh-${id}`, lufs: -20, gain_db: 4, tp: -1.5, group: g, slot };
    });
  }
  const set: SetDoc = {
    schema: 1,
    song: 'test',
    sr: 48000,
    duration_s: D,
    slice_s: 2,
    slices,
    lead_in_s: 0.12,
    lead_out_s: 0.02,
    segment_samples: 102720,
    listen: { slice_s: 10, slices: listenSlices, bitrate: 96 },
    scrub: { bitrate: 48, pack_size: packSize },
    lufs_target: -16,
    order: [...variants],
    groups,
    variants: vmap,
    excluded: [],
  };
  const objects = new Map<string, ArrayBuffer>();
  for (const g of groups) {
    for (let i = 0; i < slices; i++) {
      const members = g.variants.map((v) => descriptorBytes({ v, tier: 's', i }));
      objects.set(packUrl(set, g.hash, i), packBytes(members));
    }
  }
  for (const v of variants) {
    for (let k = 0; k < listenSlices; k++) {
      objects.set(listenUrl(set, vmap[v]!.render_hash, k), descriptorBytes({ v, tier: 'l', i: k }, 256));
    }
  }
  return { set, objects };
}

// ---------------------------------------------------------------- fetch

export class FakeFetch {
  log: { url: string; range?: string }[] = [];
  /** latency in ms per request (uses fake timers); 0 = resolve on next microtask */
  latencyMs = 0;
  failUrls = new Set<string>();
  constructor(private objects: Map<string, ArrayBuffer>) {}

  readonly fn: FetchFn = (url, init) => {
    const range = init.headers?.Range;
    this.log.push({ url, range });
    const body = this.objects.get(url);
    const done = new Promise<{ ok: boolean; status: number; arrayBuffer(): Promise<ArrayBuffer> }>((resolve, reject) => {
      const finish = () => {
        if (init.signal?.aborted) {
          reject(new DOMException('aborted', 'AbortError'));
          return;
        }
        if (!body || this.failUrls.has(url)) {
          resolve({ ok: false, status: 404, arrayBuffer: async () => new ArrayBuffer(0) });
          return;
        }
        if (range) {
          const m = /bytes=(\d+)-(\d+)/.exec(range)!;
          const a = Number(m[1]);
          const b = Math.min(Number(m[2]), body.byteLength - 1);
          resolve({ ok: true, status: 206, arrayBuffer: async () => body.slice(a, b + 1) });
          return;
        }
        resolve({ ok: true, status: 200, arrayBuffer: async () => body.slice(0) });
      };
      if (this.latencyMs > 0) setTimeout(finish, this.latencyMs);
      else queueMicrotask(finish);
    });
    return done;
  };
}

// ---------------------------------------------------------------- decoder

export class FakeDecoder implements Decoder {
  readonly kind = 'native' as const;
  readonly stats = { decoded: 0, msTotal: 0, errors: 0, queued: 0, active: 0 };
  latencyMs = 0;
  private q = new DecodeQueue(4);
  decode(bytes: ArrayBuffer, priority: number): Promise<BufferLike> {
    return this.q.submit(priority, () => new Promise<BufferLike>((resolve) => {
      const finish = () => {
        this.stats.decoded++;
        resolve(descriptorToBuffer(bytes));
      };
      if (this.latencyMs > 0) setTimeout(finish, this.latencyMs);
      else queueMicrotask(finish);
    }));
  }
}

/** flush microtasks a few times */
export async function flush(n = 5): Promise<void> {
  for (let i = 0; i < n; i++) await Promise.resolve();
}
