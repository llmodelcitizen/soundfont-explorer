import { describe, expect, it } from 'vitest';
import { tierLabel, tierTitle } from '../../src/ui/nowplaying';
import { makeSet } from './fakes';

const { set } = makeSet(['a'], 8);

describe('now playing tier', () => {
  it('names the tier by what it is doing: listening / scrubbing', () => {
    expect(tierLabel('l', set)).toBe('listening · 96k');
    expect(tierLabel('s', set)).toBe('scrubbing · 48k');
  });

  it('shows nothing while no audio is playing', () => {
    expect(tierLabel(null, set)).toBe('');
  });

  it('names both tiers the same way in the tooltip', () => {
    expect(tierTitle(set)).toBe('audio tier: scrubbing (48 kbps) or listening (96 kbps)');
    for (const word of ['scrub (', 'listen (']) expect(tierTitle(set)).not.toContain(word);
  });
});
