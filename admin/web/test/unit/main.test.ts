import { afterEach, describe, expect, it, vi } from 'vitest';
import { Fail, FakeFetch, drain, libraryDoc, until } from './fakes';

// main.ts boots on import: stage #app and the fake API first, then import a fresh copy.
async function boot(ff: FakeFetch): Promise<HTMLElement> {
  document.body.innerHTML = '<div id="app"></div>';
  vi.stubGlobal('fetch', ff.fn);
  vi.resetModules();
  await import('../../src/main');
  const app = document.getElementById('app')!;
  await until(() => app.querySelector('header.topbar') !== null, 500);
  return app;
}

const api = () => new FakeFetch()
  .on('GET', '/api/bootstrap', () => ({ phase: 'ready', pct: 100, msg: 'ready' }))
  .on('GET', '/api/me', () => ({ email: 'admin@example.com', hostname: 'box' }))
  .on('GET', '/api/library', () => libraryDoc([]));

const button = (label: string) =>
  [...document.querySelectorAll('button')].find((b) => b.textContent === label)!;
const status = () => document.querySelector('header.topbar .statusline')!;

afterEach(() => {
  document.body.replaceChildren();
  vi.unstubAllGlobals();
});

describe('boot failures', () => {
  it('leaves an expired session to the login redirect instead of painting over the page', async () => {
    const ff = api().on('GET', '/api/me', () => { throw new Fail(401, 'session expired'); });
    document.body.innerHTML = '<div id="app"></div>';
    vi.stubGlobal('fetch', ff.fn);
    vi.stubGlobal('location', { href: '' });
    vi.resetModules();
    await import('../../src/main');
    const app = document.getElementById('app')!;
    await until(() => location.href === '/auth/login'); // api.ts sent us to the login flow
    await new Promise((r) => setTimeout(r, 0)); // let boot()'s rejection settle
    expect(app.textContent).toBe('');
  });

  it('still reports a real boot failure', async () => {
    const ff = api().on('GET', '/api/me', () => { throw new Fail(500, 'identity oracle down'); });
    document.body.innerHTML = '<div id="app"></div>';
    vi.stubGlobal('fetch', ff.fn);
    vi.resetModules();
    await import('../../src/main');
    const app = document.getElementById('app')!;
    await until(() => app.querySelector('.notice') !== null);
    expect(app.textContent).toBe('admin failed to start: identity oracle down');
  });
});

describe('tab switching', () => {
  it('says so when a view throws while rendering instead of going blank', async () => {
    const ff = api()
      // a doc without `songs` (an older/newer server) makes RunsView throw inside render(),
      // past the try that covers its own GET
      .on('GET', '/api/render/songs', () => ({ render_enabled: true }))
      .on('GET', '/api/runs', () => ({ runs: [] }));
    await boot(ff);
    button('Renders').click();
    await until(() => document.querySelector('main .notice') !== null);
    expect(document.querySelector('main .notice')!.textContent)
      .toMatch(/^renders failed to load: /);
  });

  it('does not paint a late failure over the tab the user switched to', async () => {
    // The tab handlers do not await load(), so a load that fails after the user has moved
    // on used to replace whatever tab is now on screen with its own notice.
    let release: ((v: unknown) => void) | null = null;
    let first = true;
    const ff = api()
      .on('GET', '/api/library', () => {
        if (first) {
          first = false;
          return libraryDoc([]); // the initial Library render
        }
        return new Promise((r) => { release = r; });
      })
      .on('GET', '/api/render/songs', () => ({ render_enabled: true, songs: [] }))
      .on('GET', '/api/runs', () => ({ runs: [] }));
    await boot(ff);
    button('Library').click(); // a second /api/library that hangs
    await until(() => release !== null);
    button('Renders').click(); // ... while the user moves on
    await until(() => document.querySelector('main .runs') !== null);

    // a doc without `entries` (an older/newer server) makes the library load throw
    release!({ updated_at: null, preview: { fluidsynth: true, ffmpeg: true, gm_sf2: true } });
    await drain();
    expect(document.querySelector('main > .notice')).toBeNull();
    expect(document.querySelector('main .runs')).not.toBeNull();
  });
});

describe('shell buttons', () => {
  it('Update & restart comes back (with the error) when the request fails', async () => {
    const ff = api().on('POST', '/api/update', () => { throw new Fail(500, 'no update path'); });
    await boot(ff);
    const update = button('Update & restart');
    update.click();
    expect(update.disabled).toBe(true);
    await until(() => ff.count('POST', '/api/update') === 1);
    await until(() => !update.disabled);
    expect(status().textContent).toBe('update: no update path');
    expect(status().classList.contains('error')).toBe(true);
  });

  it('Shut down box stays on the shell when the request fails', async () => {
    const ff = api().on('POST', '/api/shutdown', () => { throw new Fail(500, 'not on EC2 (no instance metadata)'); });
    const app = await boot(ff);
    vi.stubGlobal('confirm', () => true);
    button('Shut down box').click();
    await until(() => ff.count('POST', '/api/shutdown') === 1);
    await until(() => status().textContent !== '');
    expect(status().textContent).toBe('shutdown: not on EC2 (no instance metadata)');
    // the topbar CSS caps the visible width of this 45-char message, so the full text has
    // to stay reachable somewhere
    expect(status().getAttribute('title')).toBe('shutdown: not on EC2 (no instance metadata)');
    expect(app.querySelector('header.topbar')).not.toBeNull();
  });
});
