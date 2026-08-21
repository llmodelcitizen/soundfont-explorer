/** Byte-budgeted LRU with pin counts. Pinned entries are never evicted. */
export class ByteLRU<V> {
  private map = new Map<string, { v: V; bytes: number; pins: number }>();
  private _bytes = 0;
  hits = 0;
  misses = 0;
  evictions = 0;

  constructor(public budget: number, private readonly sizeOf: (v: V) => number, private readonly onEvict?: (k: string, v: V) => void) {}

  get bytes(): number {
    return this._bytes;
  }
  get size(): number {
    return this.map.size;
  }

  get(k: string): V | undefined {
    const e = this.map.get(k);
    if (!e) {
      this.misses++;
      return undefined;
    }
    this.hits++;
    // refresh recency
    this.map.delete(k);
    this.map.set(k, e);
    return e.v;
  }

  /** like get() but does not refresh recency; still counted as hit/miss for the debug panel */
  peek(k: string): V | undefined {
    const e = this.map.get(k);
    if (e) this.hits++;
    else this.misses++;
    return e?.v;
  }

  has(k: string): boolean {
    return this.map.has(k);
  }

  set(k: string, v: V): void {
    const old = this.map.get(k);
    const bytes = this.sizeOf(v);
    if (old) {
      this._bytes -= old.bytes;
      this.map.delete(k);
      this.map.set(k, { v, bytes, pins: old.pins });
    } else {
      this.map.set(k, { v, bytes, pins: 0 });
    }
    this._bytes += bytes;
    // never evict the entry we just inserted: the caller needs it at least once, and evicting it
    // would let a request→decode→evict→request loop spin when everything else is pinned
    this.evict(k);
  }

  delete(k: string): boolean {
    const e = this.map.get(k);
    if (!e) return false;
    this._bytes -= e.bytes;
    this.map.delete(k);
    return true;
  }

  pin(k: string): void {
    const e = this.map.get(k);
    if (e) e.pins++;
  }

  unpin(k: string): void {
    const e = this.map.get(k);
    if (e && e.pins > 0) e.pins--;
  }

  isPinned(k: string): boolean {
    return (this.map.get(k)?.pins ?? 0) > 0;
  }

  /** true when the budget is exceeded only by pinned (+ the newest) entries */
  get overcommitted(): boolean {
    return this._bytes > this.budget;
  }

  private evict(except?: string): void {
    if (this._bytes <= this.budget) return;
    for (const [k, e] of this.map) {
      if (this._bytes <= this.budget) break;
      if (e.pins > 0 || k === except) continue;
      this.map.delete(k);
      this._bytes -= e.bytes;
      this.evictions++;
      this.onEvict?.(k, e.v);
    }
  }

  clear(): void {
    this.map.clear();
    this._bytes = 0;
  }
}
