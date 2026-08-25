import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LibraryView } from '../../src/library';
import { Fail, FakeFetch, entry, libraryDoc, until } from './fakes';

const doc = () => libraryDoc([
  entry('a1', 'alpha/one.mid'),
  entry('a2', 'alpha/two.mid'),
  entry('b1', 'beta/three.mid'),
]);

function mount(ff: FakeFetch): LibraryView {
  vi.stubGlobal('fetch', ff.fn);
  const view = new LibraryView();
  document.body.append(view.root);
  return view;
}

const status = (view: LibraryView) => view.root.querySelector('.statusline')!;
const search = (view: LibraryView) => view.root.querySelector<HTMLInputElement>('input[type=search]')!;
const button = (view: LibraryView, label: string) =>
  [...view.root.querySelectorAll('button')].find((b) => b.textContent === label)!;

/** Open the upload dialog and return the (detached) file picker it clicks. */
function openUpload(view: LibraryView, files: File[]): HTMLInputElement {
  let picker: HTMLInputElement | undefined;
  vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(function (this: HTMLInputElement) {
    picker = this;
  });
  button(view, 'Upload…').click();
  if (!picker) throw new Error('no file picker opened');
  Object.defineProperty(picker, 'files', { value: files });
  return picker;
}

describe('LibraryView filter box', () => {
  beforeEach(() => {
    localStorage.clear();
    // every folder open so tracks are rendered
    localStorage.setItem('sfadmin.folders.v1', JSON.stringify(['alpha', 'beta']));
  });
  afterEach(() => {
    document.body.replaceChildren();
    vi.unstubAllGlobals();
  });

  it('keeps focus and identity across the re-render each keystroke triggers', async () => {
    const ff = new FakeFetch().on('GET', '/api/library', doc);
    const view = mount(ff);
    await view.load();
    const box = search(view);
    box.focus();
    expect(document.activeElement).toBe(box);

    box.value = 'thr';
    box.dispatchEvent(new Event('input'));

    expect(view.root.querySelector('input[type=search]')).toBe(box);
    expect(document.activeElement).toBe(box);
    expect(box.value).toBe('thr');
    const names = [...view.root.querySelectorAll('.row .nm')].map((n) => n.textContent);
    expect(names).toEqual(['three.mid']);

    // a reload (e.g. after an action) keeps the toolbar too, and refreshes its counters
    ff.on('GET', '/api/library', () => libraryDoc([entry('z', 'zed.mid', {
      canon: { status: 'refused', reason: 'x', canonical_sha256: null, duration_s: null, checked_at: null },
    })]));
    await view.load();
    expect(view.root.querySelector('input[type=search]')).toBe(box);
    expect(view.root.querySelectorAll('.toolbar')).toHaveLength(1);
    const buttons = [...view.root.querySelectorAll('.toolbar button')].map((b) => b.textContent);
    expect(buttons).toContain('Canon check (1 refused)');
    expect(view.root.querySelector('.toolbar .count')?.textContent).toBe('1 tracks');
    expect(status(view)).toBeTruthy();
  });
});

describe('LibraryView upload dialog', () => {
  afterEach(() => {
    document.body.replaceChildren();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('Cancel on the directory prompt aborts the upload', async () => {
    const ff = new FakeFetch().on('GET', '/api/library', doc)
      .on('POST', '/api/library/upload', () => ({ results: [{ ok: true, path: 'one.mid' }] }));
    const view = mount(ff);
    await view.load();
    vi.stubGlobal('prompt', () => null);
    const picker = openUpload(view, [new File(['x'], 'one.mid')]);
    await picker.onchange!.call(picker, new Event('change'));
    expect(ff.count('POST', '/api/library/upload')).toBe(0);
    expect(status(view).textContent).toBe('upload cancelled');
  });

  it('an accepted prompt uploads into that directory', async () => {
    const ff = new FakeFetch().on('GET', '/api/library', doc)
      .on('POST', '/api/library/upload', () => ({ results: [{ ok: true, path: 'alpha/one.mid' }] }));
    const view = mount(ff);
    await view.load();
    vi.stubGlobal('prompt', () => 'alpha');
    const picker = openUpload(view, [new File(['x'], 'one.mid')]);
    await picker.onchange!.call(picker, new Event('change'));
    expect(ff.count('POST', '/api/library/upload')).toBe(1);
    const fd = ff.calls.at(-2)?.body as FormData; // followed by the GET /api/library reload
    expect(fd.get('dir')).toBe('alpha');
    expect(fd.getAll('files')).toHaveLength(1);
    expect(status(view).textContent).toBe('upload 1 file(s): done');
  });
});

describe('LibraryView canon poll', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    document.body.replaceChildren();
    vi.unstubAllGlobals();
  });

  it('survives a failed status GET and still reports the result', async () => {
    let n = 0;
    const ff = new FakeFetch().on('GET', '/api/library', doc)
      .on('POST', '/api/library/canon', () => ({ ok: true }))
      .on('GET', '/api/library/canon/status', () => {
        n++;
        if (n === 1) return { running: true };
        if (n === 2) throw new Error('socket hang up');
        return { running: false, result: { totals: { ok: 3 } } };
      });
    const view = mount(ff);
    await view.load();
    button(view, 'Canon check').click();
    await until(() => ff.count('GET', '/api/library/canon/status') === 1);
    await vi.advanceTimersByTimeAsync(3000);
    await until(() => /retrying/.test(status(view).textContent ?? ''));
    expect(status(view).classList.contains('error')).toBe(true);
    await vi.advanceTimersByTimeAsync(3000);
    await until(() => ff.count('GET', '/api/library') === 2); // result shown, library reloaded
    expect(status(view).textContent).toBe('canon (library totals): {"ok":3}');
    expect(status(view).classList.contains('error')).toBe(false);
    await vi.advanceTimersByTimeAsync(30000);
    expect(n).toBe(3); // and the poll stopped
  });

  it('a second canon run replaces the first poll instead of racing it', async () => {
    const ff = new FakeFetch().on('GET', '/api/library', doc)
      .on('POST', '/api/library/canon', () => ({ ok: true }))
      .on('GET', '/api/library/canon/status', () => ({ running: true }));
    const view = mount(ff);
    await view.load();
    button(view, 'Canon check').click();
    await until(() => ff.count('GET', '/api/library/canon/status') === 1);
    button(view, 'Canon check').click(); // 'c' on a selection, a double click, same thing
    await until(() => ff.count('GET', '/api/library/canon/status') === 2);

    const started = ff.count('GET', '/api/library/canon/status');
    await vi.advanceTimersByTimeAsync(3000);
    expect(ff.count('GET', '/api/library/canon/status') - started).toBe(1); // one loop, not two
    await vi.advanceTimersByTimeAsync(3000);
    expect(ff.count('GET', '/api/library/canon/status') - started).toBe(2);
  });

  it('keeps the live poll when the server refuses a second start', async () => {
    // The server allows one canon run at a time and answers 409 for a concurrent start,
    // while a full run takes minutes — so pressing 'c' or clicking again mid-run is the
    // ordinary case. The refused start must not take the running poll over: doing so
    // orphaned the run (status frozen on the 409, result never read, library never
    // reloaded) exactly the way the single-flight was written to prevent.
    let running = false;
    const ff = new FakeFetch().on('GET', '/api/library', doc)
      .on('POST', '/api/library/canon', () => {
        if (running) throw new Fail(409, 'a canon run is already in progress');
        running = true;
        return { ok: true };
      })
      .on('GET', '/api/library/canon/status', () => (running
        ? { running: true }
        : { running: false, result: { totals: { ok: 1 } } }));
    const view = mount(ff);
    await view.load();
    button(view, 'Canon check').click();
    await until(() => ff.count('GET', '/api/library/canon/status') === 1);
    button(view, 'Canon check').click(); // refused: run #1 is still going
    await until(() => /already in progress/.test(status(view).textContent ?? ''));

    running = false; // run #1 finishes server-side
    await vi.advanceTimersByTimeAsync(3000);
    await until(() => ff.count('GET', '/api/library') === 2);
    expect(status(view).textContent).toBe('canon (library totals): {"ok":1}');
    expect(status(view).classList.contains('error')).toBe(false);
  });

  it('stops on an expired session rather than retrying into the login redirect', async () => {
    const ff = new FakeFetch().on('GET', '/api/library', doc)
      .on('POST', '/api/library/canon', () => ({ ok: true }))
      .on('GET', '/api/library/canon/status', () => { throw new Fail(401, 'session expired'); });
    const view = mount(ff);
    await view.load();
    vi.stubGlobal('location', { href: '' });
    button(view, 'Canon check').click();
    await until(() => ff.count('GET', '/api/library/canon/status') === 1);
    await vi.advanceTimersByTimeAsync(3000 * 20);
    expect(ff.count('GET', '/api/library/canon/status')).toBe(1); // not 10 tries over 30 s
    expect(location.href).toBe('/auth/login');
    expect(status(view).textContent).not.toMatch(/retrying/);
  });

  it('gives up after repeated failures instead of polling forever', async () => {
    const ff = new FakeFetch().on('GET', '/api/library', doc)
      .on('POST', '/api/library/canon', () => ({ ok: true }))
      .on('GET', '/api/library/canon/status', () => { throw new Error('down'); });
    const view = mount(ff);
    await view.load();
    button(view, 'Canon check').click();
    await until(() => ff.count('GET', '/api/library/canon/status') === 1);
    await vi.advanceTimersByTimeAsync(60000);
    // the retries back off, so a minute of server hiccup does not abandon a run that a
    // full canon check would still be in the middle of
    expect(status(view).textContent).toMatch(/retrying/);
    await vi.advanceTimersByTimeAsync(120000);
    expect(ff.count('GET', '/api/library/canon/status')).toBe(10);
    expect(status(view).textContent).toMatch(/lost track of the run after 10 failed/);
    expect(status(view).classList.contains('error')).toBe(true);
    expect(ff.count('GET', '/api/library')).toBe(1); // no reload without a result
  });
});
