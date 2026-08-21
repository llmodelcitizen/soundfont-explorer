/**
 * Adaptive hold policy (plan D3 / §10 "Input policy").
 *
 *  rate-limited-cursor (first adaptiveAfterMs of a repeat run): repeats are dropped unless
 *    ≥ minDwellMs since the last move; every move commits → every font is heard ≥ 83 ms.
 *  sample-path (after adaptiveAfterMs): every repeat moves the cursor; commits are
 *    leading-edge throttled at commitHz targeting the cursor at commit time (one pending
 *    timer, coalesced); a trailing debounce commits the final cursor.
 *  Key-up resets the run and commits the end point. jump() (PgUp/PgDn/Home/End/click/search)
 *  bypasses the limiter.
 */
import { POLICY } from '../config';

export interface PolicyHost {
  /** move the cursor by delta (clamped); returns the new index */
  move(delta: number): number;
  /** move the cursor to an absolute index (clamped); returns the new index */
  moveTo(index: number): number;
  /** commit = ask the engine to play the variant at `index` (selectedAt for latency metrics) */
  commit(index: number, selectedAt: number): void;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(h: unknown): void;
}

export type PolicyMode = 'idle' | 'rate-limited-cursor' | 'sample-path';

export class InputPolicy {
  mode: PolicyMode = 'idle';
  private runStart: number | null = null;
  private lastMoveAt = -Infinity;
  private lastCommitAt = -Infinity;
  private lastCommitted: number | null = null;
  private cursor = 0;
  private throttleTimer: unknown = null;
  private settleTimer: unknown = null;
  /** counters for tests / debug */
  stats = { moves: 0, dropped: 0, commits: 0 };

  constructor(private readonly host: PolicyHost, readonly cfg = POLICY) {}

  get interval(): number {
    return 1000 / this.cfg.commitHz;
  }

  private doCommit(at: number): void {
    this.clearThrottle();
    this.lastCommitAt = at;
    this.lastCommitted = this.cursor;
    this.stats.commits++;
    this.host.commit(this.cursor, at);
  }

  private clearThrottle(): void {
    if (this.throttleTimer !== null) {
      this.host.clearTimeout(this.throttleTimer);
      this.throttleTimer = null;
    }
  }

  private clearSettle(): void {
    if (this.settleTimer !== null) {
      this.host.clearTimeout(this.settleTimer);
      this.settleTimer = null;
    }
  }

  /** cursor moved to `index` at time `at` (bookkeeping shared by every move path) */
  private moved(index: number, at: number): void {
    this.cursor = index;
    this.stats.moves++;
    this.lastMoveAt = at;
  }

  private moveBy(delta: number, at: number): void {
    this.moved(this.host.move(delta), at);
  }

  private armSettle(at: number): void {
    this.clearSettle();
    this.settleTimer = this.host.setTimeout(() => {
      this.settleTimer = null;
      if (this.lastCommitted !== this.cursor) this.doCommit(at + this.cfg.settleMs);
    }, this.cfg.settleMs);
  }

  /** A cursor step from a key event or an on-screen repeat button. */
  step(delta: number, repeat: boolean, at: number): void {
    if (!repeat || this.runStart === null) {
      // fresh press: leading edge — move and commit immediately
      this.runStart = at;
      this.mode = 'rate-limited-cursor';
      this.moveBy(delta, at);
      this.doCommit(at);
      this.armSettle(at);
      return;
    }
    const elapsed = at - this.runStart;
    if (elapsed < this.cfg.adaptiveAfterMs) {
      this.mode = 'rate-limited-cursor';
      if (at - this.lastMoveAt < this.cfg.minDwellMs) {
        this.stats.dropped++;
        return;
      }
      this.moveBy(delta, at);
      this.doCommit(at);
      this.armSettle(at);
      return;
    }
    // sample-path
    this.mode = 'sample-path';
    this.moveBy(delta, at);
    if (at - this.lastCommitAt >= this.interval) {
      this.doCommit(at);
    } else if (this.throttleTimer === null) {
      const wait = this.lastCommitAt + this.interval - at;
      this.throttleTimer = this.host.setTimeout(() => {
        this.throttleTimer = null;
        this.doCommit(this.lastCommitAt + this.interval);
      }, wait);
    }
    this.armSettle(at);
  }

  /** Bypass the limiter: PgUp/PgDn/Home/End/click/search. */
  jump(index: number, at: number): void {
    this.reset();
    this.moved(this.host.moveTo(index), at);
    this.doCommit(at);
  }

  jumpBy(delta: number, at: number): void {
    this.reset();
    this.moveBy(delta, at);
    this.doCommit(at);
  }

  /** Key released: end the run and make sure the end point plays. */
  keyup(at: number): void {
    this.reset();
    if (this.lastCommitted !== this.cursor) this.doCommit(at);
  }

  /** Forget the run without committing (e.g. focus loss or song change). */
  reset(): void {
    this.runStart = null;
    this.mode = 'idle';
    this.clearThrottle();
    this.clearSettle();
  }

  /** External cursor change (e.g. list rebuilt after filtering) without a commit. */
  syncCursor(index: number): void {
    this.cursor = index;
    this.lastCommitted = index;
  }
}
