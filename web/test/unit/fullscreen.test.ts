import { describe, expect, it, vi } from 'vitest';
import { FULLSCREEN_UNSUPPORTED, fullscreenSupported, isFullscreen, toggleFullscreen, type FullscreenDocument } from '../../src/ui/fullscreen';

const root = { nodeName: 'HTML' } as unknown as Element;

/** a browser with the standard API (Chrome, Firefox, desktop Safari 16.4+) */
function standard(): FullscreenDocument {
  const doc: FullscreenDocument = { documentElement: {}, fullscreenEnabled: true, fullscreenElement: null };
  doc.documentElement.requestFullscreen = vi.fn(function (this: unknown) {
    expect(this).toBe(doc.documentElement); // the call must keep its receiver, or Chrome throws
    doc.fullscreenElement = root;
    return Promise.resolve();
  });
  doc.exitFullscreen = vi.fn(() => {
    doc.fullscreenElement = null;
    return Promise.resolve();
  });
  return doc;
}

/** older WebKit: the prefixed spelling, and it returns undefined rather than a promise */
function webkit(): FullscreenDocument {
  const doc: FullscreenDocument = { documentElement: {}, webkitFullscreenEnabled: true, webkitFullscreenElement: null };
  doc.documentElement.webkitRequestFullscreen = vi.fn(() => {
    doc.webkitFullscreenElement = root;
  });
  doc.webkitExitFullscreen = vi.fn(() => {
    doc.webkitFullscreenElement = null;
  });
  return doc;
}

describe('full screen (issue #34)', () => {
  it('enters and leaves through the standard Fullscreen API', async () => {
    const doc = standard();
    expect(fullscreenSupported(doc)).toBe(true);
    expect(isFullscreen(doc)).toBe(false);
    expect(await toggleFullscreen(doc)).toBe(true);
    expect(isFullscreen(doc)).toBe(true);
    expect(await toggleFullscreen(doc)).toBe(true);
    expect(isFullscreen(doc)).toBe(false);
    expect(doc.documentElement.requestFullscreen).toHaveBeenCalledTimes(1);
    expect(doc.exitFullscreen).toHaveBeenCalledTimes(1);
  });

  it('uses the webkit-prefixed spelling when that is all there is', async () => {
    const doc = webkit();
    expect(fullscreenSupported(doc)).toBe(true);
    expect(await toggleFullscreen(doc)).toBe(true);
    expect(isFullscreen(doc)).toBe(true);
    expect(await toggleFullscreen(doc)).toBe(true);
    expect(isFullscreen(doc)).toBe(false);
  });

  it('honours an explicit force, so a second request cannot toggle back out', async () => {
    const doc = standard();
    expect(await toggleFullscreen(doc, true)).toBe(true);
    expect(await toggleFullscreen(doc, true)).toBe(true);
    expect(isFullscreen(doc)).toBe(true);
    expect(await toggleFullscreen(doc, false)).toBe(true);
    expect(isFullscreen(doc)).toBe(false);
  });

  it('degrades gracefully where a page cannot go full screen at all (iPhone Safari)', async () => {
    const ios: FullscreenDocument = { documentElement: {} };
    expect(fullscreenSupported(ios)).toBe(false);
    expect(isFullscreen(ios)).toBe(false);
    await expect(toggleFullscreen(ios)).resolves.toBe(false); // never throws: the caller shows the hint
    expect(FULLSCREEN_UNSUPPORTED).toMatch(/Home Screen/);
  });

  it('treats a forbidden document as unsupported even though the method exists (iframe)', async () => {
    const doc = standard();
    doc.fullscreenEnabled = false;
    expect(fullscreenSupported(doc)).toBe(false);
    expect(await toggleFullscreen(doc)).toBe(false);
    expect(doc.documentElement.requestFullscreen).not.toHaveBeenCalled();
  });

  it('reports a rejected request instead of leaking the rejection', async () => {
    const doc = standard();
    doc.documentElement.requestFullscreen = vi.fn(() => Promise.reject(new Error('Permissions check failed')));
    await expect(toggleFullscreen(doc)).resolves.toBe(false);
    expect(isFullscreen(doc)).toBe(false);
  });
});
