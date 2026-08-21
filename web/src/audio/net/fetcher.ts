/**
 * Priority fetch queue with an in-flight cap, per-URL(+range) dedup and abort support.
 * Lower priority number = more urgent. Range requests ask for bytes=[start,end] inclusive.
 */
export interface FetchOpts {
  priority: number;
  range?: { start: number; end: number };
  /** tag used by the prefetcher to find & abort groups of requests (e.g. 'listen') */
  tag?: string;
  /** never abort (packs) */
  sticky?: boolean;
}

export type FetchFn = (url: string, init: { headers?: Record<string, string>; signal?: AbortSignal }) => Promise<{
  ok: boolean;
  status: number;
  arrayBuffer(): Promise<ArrayBuffer>;
}>;

interface Pending {
  id: string;
  url: string;
  opts: FetchOpts;
  priority: number;
  resolve: (b: ArrayBuffer) => void;
  reject: (e: unknown) => void;
  promise: Promise<ArrayBuffer>;
  controller?: AbortController;
  /** order of the last priority assignment: ties are served in that order */
  seq: number;
  t0: number;
}

export class AbortedError extends Error {
  constructor(url: string) {
    super(`aborted ${url}`);
    this.name = 'AbortedError';
  }
}

export class Fetcher {
  private queue: Pending[] = [];
  /** id → queued entry (same members as `queue`) for O(1) dedup / lookup */
  private queued = new Map<string, Pending>();
  private inflight = new Map<string, Pending>();
  /** queue needs re-sorting before the next shift */
  private dirty = false;
  private seq = 0;
  stats = { requests: 0, bytes: 0, errors: 0, aborted: 0, msTotal: 0, completed: 0 };

  constructor(
    private readonly cap: number,
    private readonly fetchFn: FetchFn = (u, i) => fetch(u, { ...i, cache: 'default' } as RequestInit),
    private readonly now: () => number = () => (typeof performance !== 'undefined' ? performance.now() : Date.now()),
  ) {}

  static id(url: string, range?: { start: number; end: number }): string {
    return range ? `${url}#${range.start}-${range.end}` : url;
  }

  get(url: string, opts: FetchOpts): Promise<ArrayBuffer> {
    const id = Fetcher.id(url, opts.range);
    const live = this.inflight.get(id) ?? this.queued.get(id);
    if (live) {
      if (opts.priority < live.priority) {
        this.setPriority(live, opts.priority);
        live.opts.sticky = live.opts.sticky || opts.sticky;
      }
      return live.promise;
    }
    let resolve!: (b: ArrayBuffer) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<ArrayBuffer>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    promise.catch(() => undefined); // avoid unhandled rejections for fire-and-forget callers
    const p: Pending = { id, url, opts: { ...opts }, priority: opts.priority, resolve, reject, promise, seq: this.seq++, t0: 0 };
    this.queue.push(p);
    this.queued.set(id, p);
    this.dirty = true;
    this.stats.requests++;
    this.pump();
    return promise;
  }

  /** Re-prioritize a queued request (no-op if in flight or unknown). */
  reprioritize(url: string, priority: number, range?: { start: number; end: number }): void {
    const p = this.queued.get(Fetcher.id(url, range));
    if (p && priority < p.priority) this.setPriority(p, priority);
  }

  /** Abort queued or in-flight requests matching tag + predicate (sticky ones are skipped). */
  abortWhere(pred: (url: string, opts: FetchOpts) => boolean, force = false): number {
    let n = 0;
    this.queue = this.queue.filter((p) => {
      if ((p.opts.sticky && !force) || !pred(p.url, p.opts)) return true;
      this.queued.delete(p.id);
      p.reject(new AbortedError(p.url));
      n++;
      return false;
    });
    for (const p of [...this.inflight.values()]) {
      if ((p.opts.sticky && !force) || !pred(p.url, p.opts)) continue;
      p.controller?.abort();
      n++;
    }
    this.stats.aborted += n;
    return n;
  }

  isPending(url: string, range?: { start: number; end: number }): boolean {
    const id = Fetcher.id(url, range);
    return this.inflight.has(id) || this.queued.has(id);
  }

  get inflightCount(): number {
    return this.inflight.size;
  }
  get queuedCount(): number {
    return this.queue.length;
  }

  /** A (re)prioritised request is served after the ones already at that priority. */
  private setPriority(p: Pending, priority: number): void {
    p.priority = priority;
    p.seq = this.seq++;
    this.dirty = true;
  }

  private pump(): void {
    if (this.inflight.size >= this.cap || !this.queue.length) return;
    if (this.dirty) {
      this.queue.sort((a, b) => a.priority - b.priority || a.seq - b.seq);
      this.dirty = false;
    }
    while (this.inflight.size < this.cap && this.queue.length) {
      const p = this.queue.shift()!;
      this.queued.delete(p.id);
      this.start(p);
    }
  }

  private start(p: Pending): void {
    p.t0 = this.now();
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : undefined;
    p.controller = controller;
    this.inflight.set(p.id, p);
    const headers: Record<string, string> = {};
    if (p.opts.range) headers.Range = `bytes=${p.opts.range.start}-${p.opts.range.end}`;
    this.fetchFn(p.url, { headers, signal: controller?.signal })
      .then(async (res) => {
        if (!res.ok || (p.opts.range && res.status !== 206)) {
          throw new Error(`HTTP ${res.status} for ${p.url}${p.opts.range ? ' (range)' : ''}`);
        }
        const buf = await res.arrayBuffer();
        this.stats.bytes += buf.byteLength;
        this.stats.completed++;
        this.stats.msTotal += this.now() - p.t0;
        p.resolve(buf);
      })
      .catch((e) => {
        if (controller?.signal.aborted) p.reject(new AbortedError(p.url));
        else {
          this.stats.errors++;
          p.reject(e);
        }
      })
      .finally(() => {
        this.inflight.delete(p.id);
        this.pump();
      });
  }
}
