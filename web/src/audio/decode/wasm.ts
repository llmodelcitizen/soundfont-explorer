import type { BufferLike, ContextLike } from '../types';
import { DecodeQueue, type Decoder } from './index';

interface WorkerLike {
  postMessage(msg: unknown, transfer?: Transferable[]): void;
  onmessage: ((ev: MessageEvent) => void) | null;
  onerror?: ((ev: unknown) => void) | null;
  terminate(): void;
}

interface Slot {
  w: WorkerLike;
  busy: boolean;
  /** id of the job the worker is decoding */
  current: number | null;
  /** replacements in a row without the worker ever answering (reset by any reply) */
  failures: number;
}

const DECODE_TIMEOUT_MS = 15000;
/** consecutive crashes with no reply in between after which a slot is retired, not respawned */
export const MAX_CONSECUTIVE_FAILURES = 3;
/** an empty pool is allowed one fresh worker this often (see acquireSlot) */
export const EMPTY_POOL_PROBE_MS = 30000;

export type WorkerFactory = () => WorkerLike;

const defaultFactory: WorkerFactory = () => new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' }) as unknown as WorkerLike;

/** Pool of N decode workers fed from a priority queue. */
export class WasmDecoder implements Decoder {
  readonly kind = 'wasm' as const;
  readonly stats = { decoded: 0, msTotal: 0, errors: 0, queued: 0, active: 0 };
  private workers: Slot[] = [];
  private q: DecodeQueue;
  private nextId = 1;
  /** jobs parked until a slot frees up (see acquireSlot) */
  private freeWaiters: (() => void)[] = [];
  private waiting = new Map<number, { resolve: (v: { channelData: Float32Array[]; sampleRate: number }) => void; reject: (e: unknown) => void }>();
  /** when the pool last went empty, so a dead pool is probed rather than hammered */
  private retiredAt = 0;
  private disposed = false;

  constructor(private readonly ctx: ContextLike, private readonly poolSize = 4, private readonly factory: WorkerFactory = defaultFactory) {
    // the queue first: spawning can reach replace() (a worker's onerror), which resizes it
    this.q = new DecodeQueue(poolSize);
    for (let i = 0; i < poolSize; i++) this.workers.push(this.spawn());
  }

  private spawn(failures = 0): Slot {
    const w = this.factory();
    const slot: Slot = { w, busy: false, current: null, failures };
    w.onmessage = (ev: MessageEvent) => {
      const { id, error, channelData, sampleRate } = ev.data as { id: number; error?: string; channelData?: Float32Array[]; sampleRate?: number };
      const p = this.waiting.get(id);
      this.waiting.delete(id);
      slot.busy = false;
      slot.current = null;
      slot.failures = 0; // it answered, so the worker itself is alive (a decode error is not a crash)
      this.regrow(); // and the script loads, so any slot retired while it did not comes back
      if (!p) return;
      if (error || !channelData) p.reject(new Error(error ?? 'decode failed'));
      else p.resolve({ channelData, sampleRate: sampleRate ?? 48000 });
    };
    // a crashed worker must not leave its slot busy forever: fail the job and replace the worker
    w.onerror = () => this.replace(slot, 'worker error');
    return slot;
  }

  /**
   * Terminate a worker and put a fresh one in its slot. `crashed` says whether the worker died
   * without ever answering (onerror): only that counts toward the give-up bound. A decode
   * timeout is not evidence that the worker script is broken — a throttled background tab or a
   * slow device can blow 15 s on a healthy worker — and counting it would retire slots that
   * nothing ever re-grows, turning a passing slowdown into a decoder that stays dead until the
   * page is reloaded.
   */
  private replace(slot: Slot, why: string, crashed = true): void {
    const idx = this.workers.indexOf(slot);
    try {
      slot.w.terminate();
    } catch {
      /* ignore */
    }
    if (slot.current !== null) {
      const p = this.waiting.get(slot.current);
      this.waiting.delete(slot.current);
      p?.reject(new Error(why));
    }
    this.stats.errors++;
    // not in the pool any more: already replaced (a timeout and the crash it caused both land
    // here) or disposed — spawning would grow the pool past its size, or revive a dead decoder
    if (idx < 0) return;
    const failures = slot.failures + (crashed ? 1 : 0);
    if (failures >= MAX_CONSECUTIVE_FAILURES) {
      // the whole lineage died without ever answering (worker script blocked, wasm will not
      // instantiate, ...): another one would only die too, so drop the slot instead of looping
      this.workers.splice(idx, 1);
      // the pool is smaller now: stop admitting jobs no worker can run
      if (!this.workers.length) this.retiredAt = Date.now();
      this.q.setConcurrency(this.workers.length);
      this.wakeFreeWaiters();
      return;
    }
    this.workers[idx] = this.spawn(failures);
    this.wakeFreeWaiters(); // the fresh slot is idle
  }

  /**
   * Grow the pool back to its original size. A slot is retired when its whole lineage died
   * without ever answering, which usually means a worker script that will never load — but
   * memory pressure or a transient chunk/CSP failure looks exactly the same, and a decoder that
   * stays shrunk (or dead) until the page is reloaded is the worse failure. So a worker that
   * answers, which proves the script loads, re-arms whatever was retired while it did not.
   */
  private regrow(failures = 0): void {
    if (this.disposed || this.workers.length >= this.poolSize) return;
    while (this.workers.length < this.poolSize) this.workers.push(this.spawn(failures));
    this.q.setConcurrency(this.workers.length);
    this.wakeFreeWaiters();
  }

  /** let every parked job re-examine the pool: a slot was freed, replaced or retired */
  private wakeFreeWaiters(): void {
    const waiters = this.freeWaiters;
    this.freeWaiters = [];
    for (const w of waiters) w();
  }

  /**
   * Claim an idle slot, waiting for one if the pool is momentarily fully busy. The queue admits
   * at most workers.length jobs, so waiting is only for the window in which a slot retires after
   * a job was admitted. Never reject here just because every worker is busy: that rejection
   * reaches SegmentStore.request(), which negative-caches the key for up to 30 s and would
   * silence a segment that decodes perfectly well.
   */
  private async acquireSlot(): Promise<Slot> {
    for (;;) {
      if (!this.workers.length) {
        // Every slot was retired. A script that will never load must stay dead rather than
        // spin, but the pressure that killed the last lineage may have passed, so try a single
        // fresh worker at most once every EMPTY_POOL_PROBE_MS. It starts one crash short of the
        // bound, so a probe that dies retires again immediately instead of costing a lineage;
        // one that answers re-arms the whole pool through regrow().
        if (this.disposed || Date.now() - this.retiredAt < EMPTY_POOL_PROBE_MS) throw new Error('no decode workers left (they kept crashing)');
        this.retiredAt = Date.now();
        this.workers.push(this.spawn(MAX_CONSECUTIVE_FAILURES - 1));
        this.q.setConcurrency(this.workers.length);
      }
      const free = this.workers.find((s) => !s.busy);
      if (free) {
        free.busy = true; // claimed synchronously, so two admitted jobs cannot take one slot
        return free;
      }
      await new Promise<void>((resolve) => this.freeWaiters.push(resolve));
    }
  }

  decode(bytes: ArrayBuffer, priority: number): Promise<BufferLike> {
    return this.q.submit(priority, async () => {
      let slot = await this.acquireSlot();
      // a worker can crash between the claim and the first postMessage — its onerror is a task
      // of its own, and that replacement has no job to fail because none was registered yet.
      // Re-acquire instead of posting into a terminated worker and waiting out the 15 s timeout.
      while (!this.workers.includes(slot)) slot = await this.acquireSlot();
      const id = this.nextId++;
      slot.current = id;
      const t0 = Date.now();
      let timer: ReturnType<typeof setTimeout> | null = null;
      try {
        const res = await new Promise<{ channelData: Float32Array[]; sampleRate: number }>((resolve, reject) => {
          this.waiting.set(id, { resolve, reject });
          timer = setTimeout(() => {
            if (this.waiting.has(id)) this.replace(slot, `decode timeout after ${DECODE_TIMEOUT_MS} ms`, false);
          }, DECODE_TIMEOUT_MS);
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
        slot.current = null;
        throw e;
      } finally {
        if (timer) clearTimeout(timer);
        this.wakeFreeWaiters(); // the slot was released (by the reply handler or by the catch)
        this.stats.queued = this.q.queued;
        this.stats.active = this.q.running;
      }
    });
  }

  dispose(): void {
    this.disposed = true;
    for (const s of this.workers) s.w.terminate();
    this.workers = [];
    this.wakeFreeWaiters(); // parked jobs reject with 'no decode workers left' rather than hanging
  }
}
