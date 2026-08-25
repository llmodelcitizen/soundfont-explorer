import { afterEach, describe, expect, it, vi } from 'vitest';
import { downloadButton, tierLabel, tierTitle } from '../../src/ui/nowplaying';
import { makeSet } from './fakes';

afterEach(() => vi.unstubAllGlobals());

interface FakeEl {
  tag: string;
  className: string;
  attrs: Record<string, string>;
  children: unknown[];
}

/** The unit suite runs without a DOM; `h` needs only createElement/createTextNode. */
function stubDocument(): void {
  const create = (tag: string): FakeEl => {
    const el: FakeEl = { tag, className: '', attrs: {}, children: [] };
    return Object.assign(el, {
      style: {},
      dataset: {},
      setAttribute: (k: string, v: string) => void (el.attrs[k] = v),
      appendChild: (c: unknown) => void el.children.push(c),
      addEventListener: () => {},
    });
  };
  vi.stubGlobal('document', { createElement: create, createTextNode: (t: string) => t });
}

describe('download button', () => {
  it('is disabled and explains that downloads are unavailable', () => {
    stubDocument();
    const btn = downloadButton() as unknown as FakeEl;
    expect(btn.attrs.title).toBe('Downloads are not available for this session');
    expect(btn.attrs).toHaveProperty('disabled');
    expect(btn.children.join('')).toBe('⤓ download');
  });
});

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
