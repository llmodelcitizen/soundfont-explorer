import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RunsView } from '../../src/runs';
import { Fail, FakeFetch, until } from './fakes';

const run = (over: Record<string, unknown> = {}) => ({
  run_id: 'r1',
  state: 'succeeded',
  songs: ['a'],
  estimate: { jobs: 1, cpu_h: 0.1, usd: 0.5 },
  status_summary: { SUCCEEDED: 1 },
  published_sets: {},
  batch_job_id: null,
  submitted_at: '2026-08-24T00:00:00Z',
  finished_at: '2026-08-24T01:00:00Z',
  finisher: { ran_at: null, songs_json_published: false, error: null },
  knobs: {},
  ...over,
});

const api = () => new FakeFetch().on('GET', '/api/render/songs', () => ({ render_enabled: false, songs: [] }));

let view: RunsView | null = null;

function mount(ff: FakeFetch): RunsView {
  vi.stubGlobal('fetch', ff.fn);
  view = new RunsView();
  document.body.append(view.root);
  return view;
}

const status = (v: RunsView) => v.root.querySelector('.statusline')!;
const button = (v: RunsView, label: string) =>
  [...v.root.querySelectorAll('button')].find((b) => b.textContent === label)!;

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  view?.stop();
  view = null;
  vi.useRealTimers();
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe('RunsView poll', () => {
  it('does not re-arm after stop() lands while a tick is in flight', async () => {
    let release: (() => void) | null = null;
    const ff = api().on('GET', '/api/runs', () => (ff.count('GET', '/api/runs') === 1
      ? { runs: [] }
      : new Promise((r) => { release = () => r({ runs: [] }); })));
    const v = mount(ff);
    await v.load();
    expect(ff.count('GET', '/api/runs')).toBe(1);

    await vi.advanceTimersByTimeAsync(5000); // the tick fires and its GET hangs
    expect(ff.count('GET', '/api/runs')).toBe(2);
    v.stop(); // the user switches tabs while the GET is still in flight
    release!();
    await vi.advanceTimersByTimeAsync(60000);
    expect(ff.count('GET', '/api/runs')).toBe(2);
  });

  it('does not arm a poll when stop() lands while load() is still fetching', async () => {
    // The Renders tab calls load() without awaiting it and the other tabs call stop()
    // synchronously, so a click-through during load()'s two round-trips used to leave a
    // 5 s poll running forever against a detached tree.
    let release: (() => void) | null = null;
    const ff = new FakeFetch()
      .on('GET', '/api/render/songs', () => new Promise((r) => {
        release = () => r({ render_enabled: false, songs: [] });
      }))
      .on('GET', '/api/runs', () => ({ runs: [] }));
    const v = mount(ff);
    const p = v.load(); // the user clicks "Renders" and the songs GET hangs
    await until(() => release !== null);
    v.stop(); // ... then clicks "Library" before it answers
    release!();
    await p;
    await vi.advanceTimersByTimeAsync(60000);
    expect(ff.count('GET', '/api/runs')).toBe(0); // nothing rendered, nothing polling
  });

  it('polls every 5 s while shown, and load() resumes after stop()', async () => {
    const ff = api().on('GET', '/api/runs', () => ({ runs: [] }));
    const v = mount(ff);
    await v.load();
    await vi.advanceTimersByTimeAsync(15000);
    expect(ff.count('GET', '/api/runs')).toBe(4);
    v.stop();
    await vi.advanceTimersByTimeAsync(15000);
    expect(ff.count('GET', '/api/runs')).toBe(4);
    await v.load();
    await vi.advanceTimersByTimeAsync(5000);
    expect(ff.count('GET', '/api/runs')).toBe(6); // the load itself plus one tick — never two loops
  });

  it('renders a load() that overlaps an armed tick', async () => {
    // main.ts shows the Renders tab without stop()ing first, so re-clicking the already
    // active tab starts load() with the 5 s timer still armed. The tick re-armed through
    // stop(), which bumps pollGen, and load() then skipped its render: songs/renderEnabled
    // were updated but the submit form and the canon-stale notice stayed on screen stale,
    // with nothing to say the refresh had been dropped.
    const song = (id: string, title: string) => ({ id, title, path: 'a', duration_s: 10 });
    let release: ((v: unknown) => void) | null = null;
    const ff: FakeFetch = new FakeFetch() // annotated: the handler below refers to `ff`
      .on('GET', '/api/render/songs', () => (ff.count('GET', '/api/render/songs') === 1
        ? { render_enabled: true, songs: [song('s1', 'first song')] }
        : new Promise((r) => { release = r; })))
      .on('GET', '/api/runs', () => ({ runs: [] }));
    const v = mount(ff);
    await v.load();
    const p = v.load(); // the re-click, whose songs GET outlives the armed tick
    await until(() => release !== null);
    await vi.advanceTimersByTimeAsync(5000);
    release!({ render_enabled: true, songs: [song('s1', 'first song'), song('s2', 'new song')] });
    await p;
    const picks = [...v.root.querySelectorAll('.pick')].map((n) => n.textContent?.trim());
    expect(picks).toEqual(['first song 10s', 'new song 10s']);
  });

  it('reports a tick that throws outside renderRuns and keeps polling', async () => {
    // renderRuns catches its own GET, but the layout calls around it (scroll anchoring,
    // fitLogBox) can still throw; an uncaught await there used to reject the timer
    // callback and silently end the poll chain for the rest of the session.
    const ff = api()
      .on('GET', '/api/runs', () => ({ runs: [run()] }))
      .on('GET', '/api/runs/r1/logs', () => ({ events: [{ t: 0, msg: 'hi' }] }));
    const v = mount(ff);
    await v.load();
    button(v, 'Logs').click(); // an open log box makes the next render scroll-anchor
    await until(() => ff.count('GET', '/api/runs/r1/logs') === 1);
    vi.stubGlobal('scrollBy', () => { throw new Error('layout is gone'); });

    await vi.advanceTimersByTimeAsync(5000);
    expect(status(v).textContent).toBe('runs refresh: layout is gone');
    expect(status(v).classList.contains('error')).toBe(true);
    await vi.advanceTimersByTimeAsync(10000);
    expect(ff.count('GET', '/api/runs')).toBe(4); // load + 3 ticks: the chain survived
  });
});

describe('RunsView actions', () => {
  it('reports a finisher failure instead of "done"', async () => {
    const ff = api()
      .on('GET', '/api/runs', () => ({ runs: [run()] }))
      .on('POST', '/api/runs/r1/finish', () => { throw new Fail(500, 'publisher exploded'); });
    const v = mount(ff);
    await v.load();
    button(v, 'Run finisher').click();
    await until(() => ff.count('POST', '/api/runs/r1/finish') === 1);
    await until(() => status(v).textContent !== 'finisher running…');
    expect(status(v).textContent).toBe('finisher: publisher exploded');
    expect(status(v).classList.contains('error')).toBe(true);
    await until(() => ff.count('GET', '/api/runs') === 2); // the list is still refreshed
  });

  it('reports a refresh that throws after the finisher POST', async () => {
    // The trailing `await this.renderRuns(host)` sits outside the handler's try, and
    // renderRuns only catches its own GET — the layout work it ends with (scroll
    // anchoring, fitLogBox) rejected unhandled out of the click handler, leaving
    // "finisher done" on screen with the list never refreshed.
    const ff = api()
      .on('GET', '/api/runs', () => ({ runs: [run()] }))
      .on('GET', '/api/runs/r1/logs', () => ({ events: [{ t: 0, msg: 'hi' }] }))
      // /finish returns the updated run record (the route does): the handler reads
      // finisher.songs_json_published off it before refreshing the list (#19)
      .on('POST', '/api/runs/r1/finish', () => run({
        finisher: { ran_at: '2026-08-24T02:00:00Z', songs_json_published: true, error: null },
      }));
    const v = mount(ff);
    await v.load();
    button(v, 'Logs').click(); // an open log box makes the next render scroll-anchor
    await until(() => ff.count('GET', '/api/runs/r1/logs') === 1);
    vi.stubGlobal('scrollBy', () => { throw new Error('layout is gone'); });

    button(v, 'Run finisher').click();
    await until(() => status(v).classList.contains('error'), 200);
    expect(status(v).textContent).toBe('runs refresh: layout is gone');
  });

  it('reports a terminate failure', async () => {
    const ff = api()
      .on('GET', '/api/runs', () => ({ runs: [run({ state: 'running', status_summary: { RUNNING: 1 } })] }))
      .on('POST', '/api/runs/r1/terminate', () => { throw new Fail(502, 'batch says no'); });
    const v = mount(ff);
    await v.load();
    vi.stubGlobal('confirm', () => true);
    button(v, 'Terminate').click();
    await until(() => ff.count('POST', '/api/runs/r1/terminate') === 1);
    await until(() => status(v).textContent !== '');
    expect(status(v).textContent).toBe('terminate: batch says no');
    expect(status(v).classList.contains('error')).toBe(true);
    // the refresh moved out of the catch: the list is reloaded even when the POST failed,
    // so a run Batch terminated anyway still shows its real state
    await until(() => ff.count('GET', '/api/runs') === 2);
  });
});
