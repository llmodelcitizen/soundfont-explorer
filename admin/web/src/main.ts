import './style.css';
import { get, isSessionExpired, post, type BootstrapStatus, type Me } from './api';
import { LibraryView } from './library';
import { PublishedView } from './published';
import { RunsView } from './runs';

const app = document.getElementById('app')!;

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, attrs: Record<string, string> = {}, ...children: (Node | string)[]
): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else e.setAttribute(k, v);
  }
  e.append(...children);
  return e;
}

// ---------------------------------------------------------------- wait screen

async function waitUntilReady(): Promise<void> {
  let status: BootstrapStatus;
  try {
    status = await get<BootstrapStatus>('/api/bootstrap');
  } catch {
    status = { phase: 'starting', pct: 0, msg: 'server warming up' };
  }
  if (status.phase === 'ready') return;
  const fill = el('div');
  fill.style.width = `${status.pct}%`;
  app.replaceChildren(
    el('div', { class: 'wait' },
      el('h1', {}, 'Soundfont Explorer admin is warming up'),
      el('div', { class: 'bar' }, fill),
      el('div', { class: 'msg' }, `${status.phase} — ${status.msg}`),
    ),
  );
  await new Promise((r) => setTimeout(r, 2000));
  return waitUntilReady();
}

// ---------------------------------------------------------------- shell

function shell(me: Me): void {
  const lib = new LibraryView();
  const runs = new RunsView();
  const main = el('main', {});
  const tabs = el('nav', { class: 'tabs' });
  // Every tab shows its view with an un-awaited load(). Each load() reports its own failed
  // fetch inside the view, but a throw from the render path would reject unhandled and
  // leave the tab blank with nothing said; only the Library tab used to catch that.
  // The notice belongs to the tab that failed: a slow load answering after the user has
  // moved on must not replace whatever tab is on screen now, so paint only while the
  // failing view is still the one shown.
  const showFailure = (what: string, root: HTMLElement) => (e: Error): void => {
    if (isSessionExpired(e)) return; // already on the way to /auth/login
    if (main.firstChild !== root) return; // the user has since switched tabs
    main.replaceChildren(el('div', { class: 'notice' }, `${what} failed to load: ${e.message}`));
  };
  const views: [string, () => void][] = [
    ['Library', () => {
      runs.stop();
      main.replaceChildren(lib.root);
      lib.load().catch(showFailure('library', lib.root));
    }],
    ['Renders', () => {
      main.replaceChildren(runs.root);
      runs.load().catch(showFailure('renders', runs.root));
    }],
    ['Published', () => {
      runs.stop();
      main.replaceChildren(pub.root);
      pub.load().catch(showFailure('published', pub.root));
    }],
  ];
  const pub = new PublishedView();
  for (const [name, show] of views) {
    const b = el('button', { class: 'tab' }, name);
    b.onclick = () => {
      tabs.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
      b.classList.add('active');
      show();
    };
    tabs.append(b);
  }
  (tabs.firstChild as HTMLButtonElement).classList.add('active');
  views[0]![1]();
  const status = el('span', { class: 'statusline' });
  const note = (msg: string, isError = false): void => {
    status.textContent = msg;
    status.title = msg; // CSS ellipsises it in the topbar; hover shows the whole message
    status.classList.toggle('error', isError);
  };
  const update = el('button', {}, 'Update & restart');
  update.onclick = async () => {
    // a refused write has to say so: unhandled, the button just stayed disabled and the
    // page never reloaded, with the reason only in the devtools console (#19)
    update.disabled = true;
    try {
      await post('/api/update');
    } catch (e) {
      // nothing is restarting, so the button must come back rather than stay stuck
      update.disabled = false;
      note(`update: ${(e as Error).message}`, true);
      return;
    }
    note('update requested — reloading in a few seconds…');
    setTimeout(() => location.reload(), 4000);
  };
  const shutdown = el('button', { class: 'danger' }, 'Shut down box');
  shutdown.onclick = async () => {
    if (!confirm('Terminate the admin instance? Everything is saved in S3; relaunch with up.sh.')) return;
    try {
      await post('/api/shutdown');
    } catch (e) {
      note(`shutdown: ${(e as Error).message}`, true);
      return;                                  // the box is still up: do not say "Bye"
    }
    app.replaceChildren(el('div', { class: 'wait' },
      el('h1', {}, 'Terminating'),
      el('div', { class: 'msg' }, 'The box is going away. Bye.')));
  };
  app.replaceChildren(
    el('header', { class: 'topbar' },
      el('h1', {}, 'Soundfont Explorer admin'),
      tabs,
      el('div', { class: 'spacer' }),
      status, update, shutdown,
      el('span', { class: 'who' }, me.email),
      el('a', { href: '/auth/logout' }, 'log out'),
    ),
    main,
  );
}

async function boot(): Promise<void> {
  await waitUntilReady();
  shell(await get<Me>('/api/me'));
}

boot().catch((e: Error) => {
  // An expired session is the ordinary way boot() fails: /api/me answers 401 and api.ts has
  // already set location.href, so painting the error only flashes it up for the moment
  // before the browser leaves for the login flow.
  if (isSessionExpired(e)) return;
  app.replaceChildren(el('div', { class: 'notice' }, `admin failed to start: ${e.message}`));
});
