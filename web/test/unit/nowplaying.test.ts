import { afterEach, describe, expect, it, vi } from 'vitest';
import { downloadButton } from '../../src/ui/nowplaying';

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
