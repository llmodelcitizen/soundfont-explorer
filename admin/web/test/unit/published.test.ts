// The Published tab's side of #46: painting the table must cost one request no matter how
// many tracks are on the site, and any object count it shows must have been measured for
// that row, on purpose, moments earlier.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PublishedView } from '../../src/published';
import { Fail, FakeFetch, until } from './fakes';

const track = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  title: id,
  path: `${id}.mid`,
  variant_count: 1,
  duration_s: 60,
  has_audio: true,
  set_docs: 1,
  in_songs_json: true,
  ...over,
});

const overview = (...tracks: object[]) => ({ generated_at: 't0', tracks, discrepancies: [] });

const usage = (id: string, objects = 8820, bytes = 512 << 20) =>
  ({ id, objects, bytes, measured_at: '2026-08-25T12:00:00Z' });

function mount(ff: FakeFetch): PublishedView {
  vi.stubGlobal('fetch', ff.fn);
  const view = new PublishedView();
  document.body.append(view.root);
  return view;
}

const status = (view: PublishedView) => view.root.querySelector('.statusline')!;
const button = (view: PublishedView, label: string) =>
  [...view.root.querySelectorAll('button')].find((b) => b.textContent === label)!;
/** The audio column of row `i` — the one the overview no longer fills in. */
const audio = (view: PublishedView, i: number) =>
  view.root.querySelectorAll('tbody tr')[i]!.children[4] as HTMLElement;

const paths = (ff: FakeFetch) => ff.calls.map((c) => `${c.method} ${c.path}`);

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe('PublishedView audio column', () => {
  it('paints every row without asking what a single song weighs', async () => {
    // The defect: the server measured all of them for this one request, ~2.4M objects and
    // minutes, so the tab never finished loading. One GET, however many tracks come back.
    const ff = new FakeFetch().on('GET', '/api/published', () =>
      overview(track('alpha'), track('beta'), track('gamma')));
    const view = mount(ff);
    await view.load();
    expect(paths(ff)).toEqual(['GET /api/published']);
    expect(view.root.querySelectorAll('tbody tr')).toHaveLength(3);
    expect([...view.root.querySelectorAll('tbody button')]
      .filter((b) => b.textContent === 'measure')).toHaveLength(3);
  });

  it('lists one song when that row asks, and says when it looked', async () => {
    const ff = new FakeFetch()
      .on('GET', '/api/published', () => overview(track('alpha'), track('beta')))
      .on('GET', '/api/published/alpha/audio', () => usage('alpha'));
    const view = mount(ff);
    await view.load();
    (audio(view, 0).querySelector('button') as HTMLButtonElement).click();
    await until(() => audio(view, 0).querySelector('button') === null);

    expect(audio(view, 0).textContent).toBe('8820 objs · 512.0 MB');
    // never bare: a run publishing under this very prefix moves the number as we look at it
    expect(audio(view, 0).querySelector('span')!.title).toContain('2026-08-25T12:00:00Z');
    expect(ff.count('GET', '/api/published/beta/audio')).toBe(0);   // and only that row
  });

  it('offers nothing to measure for a track with no audio at all', async () => {
    const ff = new FakeFetch().on('GET', '/api/published', () =>
      overview(track('gone', { has_audio: false })));
    const view = mount(ff);
    await view.load();
    expect(audio(view, 0).textContent).toBe('none');
    expect(audio(view, 0).querySelector('button')).toBeNull();
  });

  it('leaves the row measurable when the listing fails, and says why', async () => {
    const ff = new FakeFetch()
      .on('GET', '/api/published', () => overview(track('alpha')))
      .on('GET', '/api/published/alpha/audio', () => { throw new Fail(500, 'AccessDenied'); });
    const view = mount(ff);
    await view.load();
    (audio(view, 0).querySelector('button') as HTMLButtonElement).click();
    await until(() => status(view).textContent === 'AccessDenied');
    expect(audio(view, 0).querySelector('button')!.textContent).toBe('measure');
    expect(audio(view, 0).querySelector('button')!.disabled).toBe(false);
  });
});

describe('PublishedView remove confirmation', () => {
  const asked: string[] = [];
  const confirming = (answer: boolean) => {
    asked.length = 0;
    vi.stubGlobal('confirm', (msg: string) => { asked.push(msg); return answer; });
  };

  it('measures the track first so the confirmation states what really goes', async () => {
    // The count used to ride along on the overview; now the delete path is the one place
    // that still needs it, and it is worth two seconds to quote a live figure.
    const ff = new FakeFetch()
      .on('GET', '/api/published', () => overview(track('alpha')))
      .on('GET', '/api/published/alpha/audio', () => usage('alpha'))
      .on('DELETE', '/api/published/alpha', () => ({ id: 'alpha', deleted_objects: 8820 }));
    confirming(true);
    const view = mount(ff);
    await view.load();
    button(view, 'Remove').click();
    await until(() => ff.count('GET', '/api/published') === 2);   // the reload afterwards

    expect(asked[0]).toContain('Deletes 8820 audio objects (512.0 MB) + set docs');
    expect(paths(ff)).toEqual([
      'GET /api/published',
      'GET /api/published/alpha/audio',
      'DELETE /api/published/alpha',
      'GET /api/published',
    ]);
  });

  it('goes ahead without a figure when the measurement fails, keeping the reason up', async () => {
    // A listing that 500s must not wedge a removal the operator can still make sense of —
    // and cancelling must leave the failure on the status line, not wipe it.
    const ff = new FakeFetch()
      .on('GET', '/api/published', () => overview(track('alpha')))
      .on('GET', '/api/published/alpha/audio', () => { throw new Fail(500, 'SlowDown'); });
    confirming(false);
    const view = mount(ff);
    await view.load();
    button(view, 'Remove').click();
    await until(() => asked.length === 1);

    expect(asked[0]).toContain('Deletes its audio objects + set docs');
    expect(asked[0]).not.toMatch(/\d+ audio objects/);
    expect(status(view).textContent).toBe('SlowDown');
    expect(ff.count('DELETE', '/api/published/alpha')).toBe(0);
    expect(button(view, 'Remove').disabled).toBe(false);
  });
});
