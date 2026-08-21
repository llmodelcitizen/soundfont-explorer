import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { InputPolicy, type PolicyHost } from '../../src/input/policy';
import { POLICY } from '../../src/config';

function host(n = 100) {
  let cursor = 0;
  const commits: { index: number; at: number }[] = [];
  const h: PolicyHost = {
    move: (d) => (cursor = Math.min(n - 1, Math.max(0, cursor + d))),
    moveTo: (i) => (cursor = Math.min(n - 1, Math.max(0, i))),
    commit: (index, at) => commits.push({ index, at }),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (t) => clearTimeout(t as ReturnType<typeof setTimeout>),
  };
  return { h, commits, cursor: () => cursor };
}

describe('InputPolicy (adaptive hold)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
  });
  afterEach(() => vi.useRealTimers());

  it('fresh press moves and commits immediately', () => {
    const { h, commits, cursor } = host();
    const p = new InputPolicy(h);
    p.step(1, false, 1000);
    expect(cursor()).toBe(1);
    expect(commits).toEqual([{ index: 1, at: 1000 }]);
    expect(p.mode).toBe('rate-limited-cursor');
  });

  it('30 Hz hold: first second rate-limited to ≥83 ms dwell, then accelerates; ≤12 commits/s; ends on the last cursor', async () => {
    const { h, commits, cursor } = host(200);
    const p = new InputPolicy(h);
    let t = 0;
    p.step(1, false, t);
    const moves: number[] = [];
    // 5 s hold at 30 Hz key repeat
    for (let i = 1; i <= 150; i++) {
      t = i * 33.33;
      vi.setSystemTime(t);
      await vi.advanceTimersByTimeAsync(33.33);
      p.step(1, true, t);
      moves.push(cursor());
    }
    const cursorAt1s = moves[29]!; // after 1 s
    // first second: every font heard ≥ 83 ms → at most 12 moves + initial
    expect(cursorAt1s).toBeLessThanOrEqual(13);
    expect(cursorAt1s).toBeGreaterThanOrEqual(10);
    // afterwards: cursor moves at key-repeat speed (30/s)
    const cursorAt5s = moves[149]!;
    expect(cursorAt5s - cursorAt1s).toBeGreaterThanOrEqual(4 * 30 - 2);
    expect(p.mode).toBe('sample-path');
    // commits ≤ 12 per second in any 1 s window
    for (let w = 0; w < 5; w++) {
      const n = commits.filter((c) => c.at >= w * 1000 && c.at < (w + 1) * 1000).length;
      expect(n).toBeLessThanOrEqual(12);
    }
    // monotone commit indices (never jumps backwards while holding ↓)
    for (let i = 1; i < commits.length; i++) expect(commits[i]!.index).toBeGreaterThanOrEqual(commits[i - 1]!.index);
    // release: the end point plays
    p.keyup(t + 5);
    expect(commits.at(-1)!.index).toBe(cursor());
    expect(p.mode).toBe('idle');
  });

  it('trailing debounce commits the final cursor even without keyup', async () => {
    const { h, commits, cursor } = host(200);
    const p = new InputPolicy(h);
    let t = 0;
    p.step(1, false, t);
    for (let i = 1; i <= 60; i++) {
      t = i * 33;
      vi.setSystemTime(t);
      await vi.advanceTimersByTimeAsync(33);
      p.step(1, true, t);
    }
    const before = commits.length;
    await vi.advanceTimersByTimeAsync(POLICY.settleMs + 5);
    expect(commits.length).toBeGreaterThanOrEqual(before);
    expect(commits.at(-1)!.index).toBe(cursor());
  });

  it('jump (PgDn/Home/End/click) bypasses the limiter and resets the run', () => {
    const { h, commits, cursor } = host();
    const p = new InputPolicy(h);
    p.step(1, false, 0);
    p.step(1, true, 10); // dropped (dwell)
    expect(cursor()).toBe(1);
    p.jumpBy(10, 20);
    expect(cursor()).toBe(11);
    expect(commits.at(-1)).toEqual({ index: 11, at: 20 });
    p.jump(0, 30);
    expect(cursor()).toBe(0);
    expect(commits.at(-1)).toEqual({ index: 0, at: 30 });
    expect(p.mode).toBe('idle');
  });

  it('repeat events arriving without a keydown (touch buttons) start a run too', () => {
    const { h, commits } = host();
    const p = new InputPolicy(h);
    p.step(1, true, 0);
    p.step(1, true, 33);
    p.step(1, true, 66);
    p.step(1, true, 99);
    expect(commits.map((c) => c.index)).toEqual([1, 2]); // 0 and 99 ms (dwell 83)
  });
});
