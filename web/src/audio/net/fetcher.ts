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
  started: boolean;
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
  private inflight = new Map<string, Pending>();
  stats = { requests: 0, bytes: 0, errors: 0, aborted: 0, msTotal: 0, completed: 0, lastProtocol: '' };

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
    const live = this.inflight.get(id) ?? this.queue.find((p) => p.id === id);
    if (live) {
      if (opts.priority < live.priority) {
        live.priority = opts.priority;
        live.opts.sticky = live.opts.sticky || opts.sticky;
        this.sort();
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
    const p: Pending = { id, url, opts: { ...opts }, priority: opts.priority, resolve, reject, promise, started: false, t0: 0 };
    this.queue.push(p);
    this.stats.requests++;
    this.sort();
    this.pump();
    return promise;
  }

  /** Re-prioritize a queued request (no-op if in flight or unknown). */
  reprioritize(url: string, priority: number, range?: { start: number; end: number }): void {
    const id = Fetcher.id(url, range);
    const p = this.queue.find((q) => q.id === id);
    if (p && p.priority !== priority) {
      p.priority = priority;
      this.sort();
    }
  }

  /** Abort queued or in-flight requests matching tag + predicate (sticky ones are skipped). */
  abortWhere(pred: (url: string, opts: FetchOpts) => boolean): number {
    let n = 0;
    this.queue = this.queue.filter((p) => {
      if (p.opts.sticky || !pred(p.url, p.opts)) return true;
      p.reject(new AbortedError(p.url));
      n++;
      return false;
    });
    for (const p of [...this.inflight.values()]) {
      if (p.opts.sticky || !pred(p.url, p.opts)) continue;
      p.controller?.abort();
      n++;
    }
    this.stats.aborted += n;
    return n;
  }

  isPending(url: string, range?: { start: number; end: number }): boolean {
    const id = Fetcher.id(url, range);
    return this.inflight.has(id) || this.queue.some((p) => p.id === id);
  }

  get inflightCount(): number {
    return this.inflight.size;
  }
  get queuedCount(): number {
    return this.queue.length;
  }

  private sort(): void {
    this.queue.sort((a, b) => a.priority - b.priority);
  }

  private pump(): void {
    while (this.inflight.size < this.cap && this.queue.length) {
      const p = this.queue.shift()!;
      this.start(p);
    }
  }

  private start(p: Pending): void {
    p.started = true;
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
