import { describe, expect, it } from 'vitest';
import { Timeline } from '../../src/audio/clock';

describe('Timeline', () => {
  it('tracks position in seconds, pause/seek bump gen', () => {
    let t = 10;
    const tl = new Timeline(() => t, 100);
    expect(tl.playing).toBe(false);
    expect(tl.position()).toBe(0);
    tl.play();
    expect(tl.gen).toBe(1);
    t = 15;
    expect(tl.position()).toBeCloseTo(5);
    expect(tl.timeAt(7)).toBeCloseTo(17);
    tl.pause();
    expect(tl.position()).toBeCloseTo(5);
    t = 20;
    expect(tl.position()).toBeCloseTo(5);
    tl.seek(50);
    expect(tl.position()).toBe(50);
    tl.play();
    t = 21;
    expect(tl.position()).toBeCloseTo(51);
    expect(tl.gen).toBe(4);
  });

  it('clamps at the end when not looping and reports ended', () => {
    let t = 0;
    const tl = new Timeline(() => t, 10);
    tl.play();
    t = 12;
    expect(tl.position()).toBe(10);
    expect(tl.ended()).toBe(true);
    expect(tl.unwrapped()).toBe(12);
  });

  it('loops: position wraps, rebase slides origin without changing absolute times', () => {
    let t = 0;
    const tl = new Timeline(() => t, 10);
    tl.loop = true;
    tl.play();
    t = 23;
    expect(tl.position()).toBeCloseTo(3);
    const before = tl.timeAt(23); // unwrapped 23 → ctx 23
    expect(tl.rebase()).toBe(2);
    expect(tl.position()).toBeCloseTo(3);
    expect(tl.unwrapped()).toBeCloseTo(3);
    expect(tl.timeAt(3)).toBeCloseTo(before);
    expect(tl.ended()).toBe(false);
  });

  it('play after end restarts from 0', () => {
    let t = 0;
    const tl = new Timeline(() => t, 10);
    tl.play();
    t = 11;
    tl.pause();
    expect(tl.position()).toBe(10);
    tl.play();
    expect(tl.position()).toBeCloseTo(0);
  });
});
