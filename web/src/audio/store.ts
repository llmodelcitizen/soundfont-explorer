/**
 * SegmentStore: (variant, tier, slice) → decoded buffer, through the compressed-bytes cache,
 * the priority fetcher (whole packs or Range-per-member) and the decoder.
 *
 * request() coalesces: one fetch per pack/member, one decode per key. Callers that just need
 * "tell me when something new is decoded" subscribe with onDecoded().
 */
import { CACHE, NET } from '../config';
import type { SetDoc } from '../contracts/set';
import { listenUrl, packUrl } from '../contracts/set';
import { ByteLRU } from './cache/lru';
import type { Decoder } from './decode';
import { AbortedError, Fetcher } from './net/fetcher';
import { headerBytesFor, memberCount, memberRange, parseHeader, splitPack, type PackHeader } from './net/packs';
import { bufferBytes, keyStr, type BufferLike, type SegKey } from './types';

export interface Want {
  key: SegKey;
  priority: number;
  /** bytes only (compressed cache), no decode — for neighbours beyond the decode radius */
  fetchOnly?: boolean;
}

const BACKOFF_BASE_MS = 1000;
const BACKOFF_MAX_MS = 30000;

interface Located {
  url: string;
  /** member slot for packs; -1 for listen objects */
  slot: number;
  /** compressed cache id */
  cid: string;
}

export class SegmentStore {
  readonly decoded: ByteLRU<BufferLike>;
  readonly compressed: ByteLRU<ArrayBuffer>;
  private decoding = new Map<string, Promise<BufferLike>>();
  private packHeaders = new Map<string, PackHeader>();
  /** whole packs already split into the compressed cache (a re-fetch is a new ArrayBuffer) */
  private ingested = new WeakSet<ArrayBuffer>();
  private listeners = new Set<(key: SegKey, buf: BufferLike) => void>();
  /**
   * negative cache: key → {fails, until(ms)} so a 404/decoder error is not retried every tick.
   * `kind` is what failed last: a successful byte fetch clears a fetch failure but not a decode
   * one, so a member whose bytes are fine and whose opus is corrupt keeps ratcheting.
   */
  private failed = new Map<string, { fails: number; until: number; kind: 'fetch' | 'decode' }>();
  /** byte-only prefetches in flight, keyed like `failed`: one failure per request, not per tick */
  private fetchingOnly = new Map<string, Promise<unknown>>();
  stats = { decodedOk: 0, decodeErrors: 0, fetchErrors: 0, wholePacks: 0, rangeMembers: 0, backedOff: 0 };
  lastError: string | null = null;
  /** Range for a blind header probe: a full-size pack's header (the length table may be shorter) */
  private readonly headerProbe: { start: number; end: number };

  constructor(
    public readonly set: SetDoc,
    private readonly fetcher: Fetcher,
    private readonly decoder: Decoder,
    budgets: { decodedBytes: number; compressedBytes: number } = CACHE,
    private readonly nowMs: () => number = () => Date.now(),
  ) {
    this.decoded = new ByteLRU<BufferLike>(budgets.decodedBytes, bufferBytes);
    this.compressed = new ByteLRU<ArrayBuffer>(budgets.compressedBytes, (b) => b.byteLength);
    this.headerProbe = { start: 0, end: headerBytesFor(set.scrub.pack_size) - 1 };
  }

  // ---- lookup ---------------------------------------------------------------

  locate(key: SegKey): Located | null {
    const v = this.set.variants[key.v];
    if (!v) return null;
    if (key.tier === 'l') {
      if (key.i < 0 || key.i >= this.set.listen.slices) return null;
      const url = listenUrl(this.set, v.render_hash, key.i);
      return { url, slot: -1, cid: url };
    }
    if (key.i < 0 || key.i >= this.set.slices) return null;
    const g = this.set.groups[v.group];
    if (!g) return null;
    const url = packUrl(this.set, g.hash, key.i);
    return { url, slot: v.slot, cid: `${url}#${v.slot}` };
  }

  peek(key: SegKey): BufferLike | undefined {
    return this.decoded.peek(keyStr(key));
  }

  /** decoded and cached, without touching the hit/miss counters */
  has(key: SegKey): boolean {
    return this.decoded.has(keyStr(key));
  }

  pin(key: SegKey): void {
    this.decoded.pin(keyStr(key));
  }
  unpin(key: SegKey): void {
    this.decoded.unpin(keyStr(key));
  }

  onDecoded(cb: (key: SegKey, buf: BufferLike) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  isPending(key: SegKey): boolean {
    const loc = this.locate(key);
    if (!loc) return false;
    if (this.decoding.has(keyStr(key))) return true;
    return this.fetcher.isPending(loc.url) || (loc.slot >= 0 && this.memberRangePending(loc));
  }

  // ---- requests ------------------------------------------------------------

  /** ms until a failed key may be retried (0 = now) */
  backoffMs(key: SegKey): number {
    const f = this.failed.get(keyStr(key));
    return f ? Math.max(0, f.until - this.nowMs()) : 0;
  }

  private noteFailure(ks: string, kind: 'fetch' | 'decode'): void {
    const prev = this.failed.get(ks);
    const fails = (prev?.fails ?? 0) + 1;
    const wait = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** (fails - 1));
    this.failed.set(ks, { fails, until: this.nowMs() + wait, kind });
  }

  /** Ensure one segment is (being) decoded; resolves with the buffer. */
  request(key: SegKey, priority: number, opts: { whole?: boolean; tag?: string } = {}): Promise<BufferLike> {
    const ks = keyStr(key);
    const have = this.decoded.peek(ks);
    if (have) return Promise.resolve(have);
    const loc = this.locate(key);
    if (!loc) return Promise.reject(new Error(`no such segment ${ks}`));
    const live = this.decoding.get(ks);
    if (live) {
      // already on its way: make sure the underlying fetch is not stuck behind lower priorities
      this.fetcher.reprioritize(loc.url, priority);
      const h = this.packHeaders.get(loc.url);
      if (loc.slot >= 0 && h) this.fetcher.reprioritize(loc.url, priority, memberRange(h, loc.slot));
      return live;
    }
    const wait = this.backoffMs(key);
    if (wait > 0) {
      this.stats.backedOff++;
      return Promise.reject(new Error(`backoff ${ks} for ${wait} ms`));
    }
    // an urgent request (switch commit / audible fill) takes the whole pack: one round trip beats header+member
    if (priority <= 0 && loc.slot >= 0) opts = { ...opts, whole: true };
    let fetched = false;
    const p = this.bytesFor(loc, priority, opts)
      .then((bytes) => {
        fetched = true;
        return this.decoder.decode(bytes, priority);
      })
      .then((buf) => {
        this.decoded.set(ks, buf);
        this.failed.delete(ks);
        this.stats.decodedOk++;
        for (const l of this.listeners) l(key, buf);
        return buf;
      })
      .catch((e) => {
        if (!(e instanceof AbortedError)) {
          this.stats.decodeErrors++;
          this.lastError = `${ks}: ${(e as Error)?.message ?? String(e)}`;
          this.noteFailure(ks, fetched ? 'decode' : 'fetch');
        }
        throw e;
      })
      .finally(() => this.decoding.delete(ks));
    this.decoding.set(ks, p);
    p.catch(() => undefined);
    return p;
  }

  /**
   * Batch want-set from the prefetcher: groups pack members per object; a pack is fetched whole
   * when ≥ wholePackThreshold members are wanted (or it is already in flight), else by Range.
   */
  want(wants: Want[], tag = 'prefetch'): void {
    const byPack = new Map<string, Want[]>();
    for (const w of wants) {
      const ks = keyStr(w.key);
      if (this.decoded.has(ks) || this.decoding.has(ks)) continue;
      if (this.backoffMs(w.key) > 0) continue;
      const loc = this.locate(w.key);
      if (!loc) continue;
      if (loc.slot < 0) {
        if (!w.fetchOnly) this.request(w.key, w.priority, { tag: 'listen' }).catch(() => undefined);
        continue;
      }
      const arr = byPack.get(loc.url) ?? [];
      arr.push(w);
      byPack.set(loc.url, arr);
    }
    for (const [url, ws] of byPack) {
      const whole = ws.length >= NET.wholePackThreshold || this.fetcher.isPending(url);
      const best = Math.min(...ws.map((w) => w.priority));
      for (const w of ws) {
        if (w.fetchOnly) {
          const loc = this.locate(w.key)!;
          if (this.compressed.has(loc.cid)) continue;
          const ks = keyStr(w.key);
          // One entry per in-flight byte fetch, the way `decoding` dedups the decode path.
          // want() runs every prefetch tick and none of its earlier guards sees a byte-only
          // fetch in flight (it never populates `decoding`, and `compressed` is only filled on
          // success), so without this every tick would attach another handler to the same
          // Fetcher promise and one failed request would be recorded as a failure per tick —
          // walking the exponential backoff to its 30 s cap inside a single event.
          if (this.fetchingOnly.has(ks)) continue;
          // A bytes-only miss enters the negative cache like a request() miss does, otherwise
          // nothing stops the prefetcher from re-issuing the fetch for a missing pack every tick.
          // That is the same per-key cache request() consults, so a prefetch failure also holds
          // the audible path off this key for 1-30 s — as the non-fetchOnly branch below has
          // always done via request(). A success clears a *fetch* failure (as request() does for
          // a decode one) so unrelated transient network errors cannot ratchet the backoff up
          // over a session; a key that fetches fine and will not decode keeps its count.
          const p = this.bytesFor(loc, whole ? best : w.priority, { whole, tag })
            .then(
              () => {
                if (this.failed.get(ks)?.kind === 'fetch') this.failed.delete(ks);
              },
              (e) => {
                if (e instanceof AbortedError) return;
                this.lastError = `${ks}: ${(e as Error)?.message ?? String(e)}`;
                this.noteFailure(ks, 'fetch');
              },
            )
            .finally(() => this.fetchingOnly.delete(ks));
          this.fetchingOnly.set(ks, p);
        } else {
          this.request(w.key, whole ? best : w.priority, { whole, tag }).catch(() => undefined);
        }
      }
    }
  }

  // ---- bytes ---------------------------------------------------------------

  private memberRangePending(loc: Located): boolean {
    const h = this.packHeaders.get(loc.url);
    return this.fetcher.isPending(loc.url, h ? memberRange(h, loc.slot) : this.headerProbe);
  }

  private async bytesFor(loc: Located, priority: number, opts: { whole?: boolean; tag?: string }): Promise<ArrayBuffer> {
    const cached = this.compressed.get(loc.cid);
    if (cached) return cached;
    if (loc.slot < 0) {
      try {
        const buf = await this.fetcher.get(loc.url, { priority, tag: opts.tag ?? 'listen' });
        this.compressed.set(loc.cid, buf);
        return buf;
      } catch (e) {
        if (!(e instanceof AbortedError)) this.stats.fetchErrors++;
        throw e;
      }
    }
    // pack member
    const wholeInFlight = this.fetcher.isPending(loc.url);
    if (opts.whole || wholeInFlight) {
      try {
        const pack = await this.fetcher.get(loc.url, { priority, sticky: true, tag: 'pack' });
        // every member waiting on this pack shares one fetch promise and wakes up here with the
        // same ArrayBuffer: split it once, not once per waiter (24 splits of a 24-member pack).
        // Mark it ingested only once the split succeeded: a pack whose bytes do not parse must
        // let every waiter see the parse error, not just the first one, with the rest reporting
        // a misleading 'member N missing from pack' (and overwriting lastError with it).
        // A member evicted from the compressed cache between the split and this waiter waking
        // must be re-split from the bytes we already hold, not reported as missing from the pack.
        const first = !this.ingested.has(pack);
        if (first || !this.compressed.has(loc.cid)) {
          this.ingestPack(loc.url, pack);
          this.ingested.add(pack);
          if (first) this.stats.wholePacks++; // the same bytes split twice is not a second pack
        }
      } catch (e) {
        this.stats.fetchErrors++;
        throw e;
      }
      const got = this.compressed.get(loc.cid);
      if (!got) throw new Error(`member ${loc.slot} missing from pack ${loc.url}`);
      return got;
    }
    // Range per member: header (cached per URL) then the member
    let h = this.packHeaders.get(loc.url);
    if (!h) {
      const head = await this.fetcher.get(loc.url, { priority, range: this.headerProbe, sticky: true, tag: 'pack' });
      const need = headerBytesFor(memberCount(head));
      const full = head.byteLength >= need ? head : await this.fetcher.get(loc.url, { priority, range: { start: 0, end: need - 1 }, sticky: true, tag: 'pack' });
      h = parseHeader(full);
      this.packHeaders.set(loc.url, h);
    }
    try {
      const member = await this.fetcher.get(loc.url, { priority, range: memberRange(h, loc.slot), tag: opts.tag ?? 'prefetch' });
      this.compressed.set(loc.cid, member);
      this.stats.rangeMembers++;
      return member;
    } catch (e) {
      if (!(e instanceof AbortedError)) this.stats.fetchErrors++;
      throw e;
    }
  }

  private ingestPack(url: string, pack: ArrayBuffer): void {
    const members = splitPack(pack);
    this.packHeaders.set(url, parseHeader(pack));
    members.forEach((m, j) => this.compressed.set(`${url}#${j}`, m));
  }

  /** Abort fetches whose tag matches and whose URL is not in keep (packs are sticky anyway). */
  abortListen(keep: Set<string>): number {
    return this.fetcher.abortWhere((url, o) => o.tag === 'listen' && !keep.has(url));
  }

  /** Song switch: drop everything still queued for this store, sticky packs included. */
  abortAll(): number {
    this.listeners.clear();
    return this.fetcher.abortWhere(() => true, true);
  }
}
