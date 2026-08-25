import { afterEach, describe, expect, it, vi } from 'vitest';
import { Fail, FakeFetch, libraryDoc, until } from './fakes';

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
    expect(app.querySelector('header.topbar')).not.toBeNull();
  });
});
