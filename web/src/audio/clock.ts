/**
 * Timeline: song position ⇄ AudioContext time, in SECONDS (never frames; decoded buffers
 * may come back at the context rate, e.g. 44.1 k on some routes).
 *
 * Unwrapped position u = t − origin grows monotonically while playing; when looping the
 * song position is u mod duration and `rebase()` slides origin by whole loops so u stays small
 * without changing any already-scheduled absolute times.
 */
export class Timeline {
  /** bumped on play/pause/seek: chains built for an older gen are stale */
  gen = 0;
  loop = false;
  private origin = 0;
  private pausedAt: number | null = 0;

  constructor(private readonly now: () => number, public duration: number) {}

  get playing(): boolean {
    return this.pausedAt === null;
  }

  /** song position in [0, duration] */
  position(at: number = this.now()): number {
    if (this.pausedAt !== null) return this.pausedAt;
    const u = at - this.origin;
    if (this.loop && this.duration > 0) return ((u % this.duration) + this.duration) % this.duration;
    return Math.min(Math.max(u, 0), this.duration);
  }

  /** unwrapped position (may exceed duration when looping, may be > duration when ended) */
  unwrapped(at: number = this.now()): number {
    if (this.pausedAt !== null) return this.pausedAt;
    return at - this.origin;
  }

  /** context time at which unwrapped position u plays */
  timeAt(u: number): number {
    if (this.pausedAt !== null) return this.now() + (u - this.pausedAt);
    return this.origin + u;
  }

  play(at: number = this.now()): void {
    if (this.pausedAt === null) return;
    if (!this.loop && this.pausedAt >= this.duration) this.pausedAt = 0;
    this.origin = at - this.pausedAt;
    this.pausedAt = null;
    this.gen++;
  }

  pause(at: number = this.now()): void {
    if (this.pausedAt !== null) return;
    this.pausedAt = this.position(at);
    this.gen++;
  }

  seek(p: number, at: number = this.now()): void {
    p = Math.min(Math.max(p, 0), this.duration);
    if (this.pausedAt === null) this.origin = at - p;
    else this.pausedAt = p;
    this.gen++;
  }

  /** slide origin by whole loops; returns the number of loops consumed */
  rebase(at: number = this.now()): number {
    if (this.pausedAt !== null || !this.loop || this.duration <= 0) return 0;
    const n = Math.floor((at - this.origin) / this.duration);
    if (n > 0) this.origin += n * this.duration;
    return Math.max(n, 0);
  }

  /** true once a non-looping timeline has run past the end */
  ended(at: number = this.now()): boolean {
    return this.pausedAt === null && !this.loop && at - this.origin >= this.duration;
  }
}
