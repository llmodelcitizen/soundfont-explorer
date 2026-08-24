import './style.css';
import { get, post, type BootstrapStatus, type Me } from './api';

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
  const main = el('main', {},
    el('div', { class: 'notice' }, 'Library UI lands in the next milestone.'));
  const update = el('button', {}, 'Update & restart');
  update.onclick = async () => {
    update.disabled = true;
    await post('/api/update');
    setTimeout(() => location.reload(), 4000);
  };
  const shutdown = el('button', { class: 'danger' }, 'Shut down box');
  shutdown.onclick = async () => {
    if (!confirm('Terminate the admin instance? Everything is saved in S3; relaunch with up.sh.')) return;
    await post('/api/shutdown');
    app.replaceChildren(el('div', { class: 'wait' },
      el('h1', {}, 'Terminating'),
      el('div', { class: 'msg' }, 'The box is going away. Bye.')));
  };
  app.replaceChildren(
    el('header', { class: 'topbar' },
      el('h1', {}, 'Soundfont Explorer admin'),
      el('div', { class: 'spacer' }),
      update, shutdown,
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

boot();
