import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LibraryView } from '../../src/library';
import { FakeFetch, entry, libraryDoc } from './fakes';

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
