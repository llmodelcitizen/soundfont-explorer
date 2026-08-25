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
});
