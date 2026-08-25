// Render-runs view: submit form (tree of canon-ok songs + tuning knobs, server-checked
// estimate) and the run list with live status, log tail, terminate/finish.
import { get, post } from './api';

interface RenderSong {
  id: string;
  title: string;
  path: string | null;
  duration_s: number;
}

interface Run {
  run_id: string;
  state: string;
  songs: string[];
  estimate: { jobs: number; cpu_h: number; usd: number };
  status_summary: Record<string, number>;
  published_sets: Record<string, string>;
  batch_job_id: string | null;
  submitted_at: string;
  finished_at: string | null;
  finisher: { ran_at: string | null; songs_json_published: boolean; error: string | null };
  knobs: Record<string, unknown>;
}

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

function input(value: string, attrs: Record<string, string> = {}): HTMLInputElement {
  return el('input', { value, ...attrs });
}

export class RunsView {
  root = el('div', { class: 'runs' });
  private songs: RenderSong[] = [];
  private renderEnabled = false;
  private missingCanon = 0;
  private picked = new Set<string>();
  private status = el('span', { class: 'statusline' });
  private timer: number | null = null;
  private pollGen = 0; // bumped by stop(): a tick already in flight must not re-arm
  private openLogs = new Set<string>();

  constructor() {
    const resize = () => {
      this.root.querySelectorAll<HTMLElement>('.runlog:not([hidden])')
        .forEach((box) => this.fitLogBox(box));
    };
    window.addEventListener('resize', resize);
    window.visualViewport?.addEventListener('resize', resize);
  }

  async load(): Promise<void> {
    // The shell shows this view with an un-awaited load() and leaves it with a synchronous
    // stop(), so a tab switch can land during either round-trip below. Without the
    // generation check, load() would go on to arm a poll for a view nobody is looking at.
    const gen = this.pollGen;
    try {
      const doc = await get<{ render_enabled: boolean; missing_canon?: number; songs: RenderSong[] }>('/api/render/songs');
      this.songs = doc.songs;
      this.renderEnabled = doc.render_enabled;
      this.missingCanon = doc.missing_canon ?? 0;
    } catch (e) {
      this.root.replaceChildren(el('div', { class: 'notice' },
        `render songs unavailable: ${(e as Error).message}`));
      return;
    }
    if (gen !== this.pollGen) return;
    await this.render();
    if (gen !== this.pollGen) return;
    this.poll();
  }

  stop(): void {
    this.pollGen++;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private note(msg: string, isError = false): void {
    this.status.textContent = msg;
    this.status.classList.toggle('error', isError);
  }

  private poll(): void {
    // Clear the armed timer directly rather than through stop(): stop() also bumps pollGen,
    // and a re-arm that bumps it invalidates a load() that is still fetching — re-clicking
    // the already-active Renders tab starts exactly that, and the render was then skipped,
    // leaving a stale submit form and canon-stale notice on screen. Only leaving the view
    // (stop()) may cancel a generation.
    if (this.timer !== null) clearTimeout(this.timer);
    const gen = this.pollGen;
    this.timer = window.setTimeout(async () => {
      this.timer = null;
      try {
        const list = this.root.querySelector('.runlist');
        if (list) await this.renderRuns(list as HTMLElement);
      } catch (e) {
        // renderRuns handles its own GET; what is left are the layout calls around it
        // (scroll anchoring, fitLogBox). Rejecting here would end the poll chain for the
        // rest of the session with nothing on screen to say so.
        this.note(`runs refresh: ${(e as Error).message}`, true);
      }
      // stop() (tab switch) or a fresh load() may have landed while renderRuns was in
      // flight; clearTimeout alone cannot catch that, so re-arm only for our generation
      if (gen === this.pollGen) this.poll();
    }, 5000);
  }

  private async render(): Promise<void> {
    const form = this.form();
    const runlist = el('div', { class: 'runlist' });
    await this.renderRuns(runlist);
    this.root.replaceChildren(
      el('div', { class: 'toolbar' }, el('h2', {}, 'Render runs'), this.status),
      this.missingCanon > 0
        ? el('div', { class: 'notice stale' },
          `${this.missingCanon} canon-ok tracks are missing from this list — it predates `
          + 'the last canon run. Run a Canon check on the Library tab, then reload.')
        : el('span', {}),
      this.renderEnabled ? form : el('div', { class: 'notice' },
        'Render fleet is not deployed (enable_render_fleet) — submission unavailable.'),
      runlist);
  }

  // ---------------------------------------------------------------- submit form

  private form(): HTMLElement {
    const byDir = new Map<string, RenderSong[]>();
    for (const s of this.songs) {
      const dir = s.path ?? '';
      (byDir.get(dir) ?? byDir.set(dir, []).get(dir)!).push(s);
    }
    const tree = el('div', { class: 'picktree' });
    const boxes = new Map<string, HTMLInputElement>();
    for (const dir of [...byDir.keys()].sort()) {
      const files = byDir.get(dir)!;
      const dirBox = el('input', { type: 'checkbox' });
      dirBox.onchange = () => {
        for (const s of files) {
          boxes.get(s.id)!.checked = dirBox.checked;
          if (dirBox.checked) this.picked.add(s.id);
          else this.picked.delete(s.id);
        }
        count.textContent = `${this.picked.size} selected`;
      };
      const details = el('details', {},
        el('summary', {}, dirBox, ` ${dir === '' ? '(top level)' : dir} `,
          el('span', { class: 'count' }, String(files.length))));
      for (const s of files) {
        const box = el('input', { type: 'checkbox' });
        boxes.set(s.id, box);
        box.onchange = () => {
          if (box.checked) this.picked.add(s.id);
          else this.picked.delete(s.id);
          count.textContent = `${this.picked.size} selected`;
        };
        details.append(el('label', { class: 'pick' }, box, ` ${s.title} `,
          el('span', { class: 'count' }, `${Math.round(s.duration_s)}s`)));
      }
      tree.append(details);
    }
    const count = el('span', { class: 'count' }, '0 selected');

    const shards = input('8', { size: '3' });
    const maxUsd = input('60', { size: '5' });
    const engines = input('', { placeholder: 'all engines', size: '18' });
    const limit = input('', { placeholder: 'no limit', size: '6' });
    const itypes = input('', { placeholder: 'c7a.48xlarge,… (terraform default)', size: '28' });
    const vcpus = input('', { placeholder: '90', size: '4' });
    const mem = input('', { placeholder: '170000', size: '7' });
    const estOut = el('span', { class: 'count' });

    const body = () => ({
      songs: [...this.picked],
      shards: Number(shards.value) || 8,
      max_usd: Number(maxUsd.value) || 60,
      engines: engines.value.trim() ? engines.value.split(',').map((s) => s.trim()) : null,
      limit: limit.value.trim() || null,
      instance_types: itypes.value.trim() ? itypes.value.split(',').map((s) => s.trim()) : null,
      shard_vcpus: vcpus.value.trim() || null,
      shard_memory_mib: mem.value.trim() || null,
    });

    const estimate = el('button', {}, 'Estimate');
    estimate.onclick = async () => {
      try {
        const p = await post<{ jobs: number; cpu_h: number; usd: number; shards: unknown[] }>(
          '/api/render/plan', body());
        estOut.textContent = `${p.jobs} jobs · ${p.cpu_h} CPU-h · ~$${p.usd} across ${p.shards.length} shard(s)`;
      } catch (e) {
        this.note((e as Error).message, true);
      }
    };
    const submit = el('button', { class: 'danger' }, 'Submit to fleet');
    submit.onclick = async () => {
      if (!this.picked.size) return this.note('no songs selected', true);
      if (!confirm(`Submit ${this.picked.size} song(s) to the burst fleet? This spends real money.`)) return;
      submit.disabled = true;
      try {
        const run = await post<Run>('/api/runs', body());
        this.note(`submitted ${run.run_id}`);
        await this.renderRuns(this.root.querySelector('.runlist') as HTMLElement);
      } catch (e) {
        this.note((e as Error).message, true);
      } finally {
        submit.disabled = false;
      }
    };

    return el('div', { class: 'submitbox' },
      tree,
      el('div', { class: 'knobs' },
        el('label', {}, 'shards', shards), el('label', {}, 'max $', maxUsd),
        el('label', {}, 'engines', engines), el('label', {}, 'limit', limit),
        el('label', {}, 'instance types', itypes),
        el('label', {}, 'vCPU/shard', vcpus), el('label', {}, 'MiB/shard', mem),
        estimate, submit, count, estOut));
  }

  // ---------------------------------------------------------------- run list

  private fitLogBox(box: HTMLElement, bringIntoView = false): void {
    const viewport = window.visualViewport;
    const viewportHeight = viewport?.height ?? window.innerHeight;
    const headerHeight = document.querySelector<HTMLElement>('header.topbar')
      ?.getBoundingClientRect().height ?? 0;
    box.style.height = `${Math.max(120, viewportHeight - headerHeight)}px`;
    box.style.scrollMarginTop = `${headerHeight}px`;
    if (bringIntoView) box.scrollIntoView({ block: 'start' });
    box.scrollTop = box.scrollHeight;
  }

  private async refreshLogs(box: HTMLElement, rid: string, bringIntoView = false): Promise<void> {
    const text = box.querySelector<HTMLElement>('.runlog-text')!;
    try {
      const ev = await get<{ events: { t: number; msg: string }[] }>(`/api/runs/${rid}/logs`);
      text.textContent = ev.events.map((x) => x.msg).join('\n') || '(no log events yet)';
    } catch (e) {
      text.textContent = `logs unavailable: ${(e as Error).message}`;
    }
    if (box.isConnected && !box.hidden) this.fitLogBox(box, bringIntoView);
  }

  private async renderRuns(host: HTMLElement): Promise<void> {
    let runs: Run[];
    try {
      runs = (await get<{ runs: Run[] }>('/api/runs')).runs;
    } catch (e) {
      host.replaceChildren(el('div', { class: 'notice' }, (e as Error).message));
      return;
    }
    if (!runs.length) {
      host.replaceChildren(el('div', { class: 'notice' }, 'no runs yet'));
      return;
    }
    const anchor = host.querySelector<HTMLElement>('.runlog:not([hidden])');
    const anchorId = anchor?.dataset.runId;
    const anchorTop = anchor?.getBoundingClientRect().top;
    const rows = runs.slice(0, 20).map((r) => {
      const chips = Object.entries(r.status_summary)
        .filter(([, n]) => n > 0)
        .map(([k, n]) => el('span', { class: `chip c-${k.toLowerCase()}` }, `${k} ${n}`));
      const pub = Object.keys(r.published_sets).length;
      const fin = r.finisher.error
        ? el('span', { class: 'chip c-failed' }, `finisher: ${r.finisher.error.slice(0, 60)}`)
        : r.finisher.songs_json_published
          ? el('span', { class: 'chip c-succeeded' }, 'songs.json published')
          : el('span', {}, '');
      const actions = el('span', { class: 'btnrow' });
      if (!['succeeded', 'failed', 'terminated'].includes(r.state)) {
        const t = el('button', { class: 'danger' }, 'Terminate');
        t.onclick = async () => {
          if (!confirm(`Terminate run ${r.run_id}?`)) return;
          try {
            await post(`/api/runs/${r.run_id}/terminate`);
          } catch (e) {
            this.note(`terminate: ${(e as Error).message}`, true);
          }
          await this.renderRuns(host);
        };
        actions.append(t);
      } else if (!r.finisher.songs_json_published) {
        const f = el('button', {}, 'Run finisher');
        f.onclick = async () => {
          this.note('finisher running…');
          try {
            await post(`/api/runs/${r.run_id}/finish`);
            this.note('finisher done');
          } catch (e) {
            this.note(`finisher: ${(e as Error).message}`, true);
          }
          await this.renderRuns(host);
        };
        actions.append(f);
      }
      const logs = el('button', {}, 'Logs');
      const logIsOpen = this.openLogs.has(r.run_id);
      const logBox = el('div', {
        class: 'logbox runlog',
        'data-run-id': r.run_id,
        ...(logIsOpen ? {} : { hidden: '' }),
      }, el('pre', { class: 'runlog-text' }, logIsOpen ? 'loading logs…' : ''));
      if (logIsOpen) logs.textContent = 'Hide logs';
      logs.onclick = async () => {
        logBox.hidden = !logBox.hidden;
        if (logBox.hidden) {
          this.openLogs.delete(r.run_id);
          logs.textContent = 'Logs';
        } else {
          this.openLogs.add(r.run_id);
          logs.textContent = 'Hide logs';
          logBox.querySelector<HTMLElement>('.runlog-text')!.textContent = 'loading logs…';
          this.fitLogBox(logBox, true);
          await this.refreshLogs(logBox, r.run_id);
        }
      };
      actions.append(logs);
      return el('div', { class: `runrow st-${r.state}` },
        el('div', { class: 'runhead' },
          el('strong', {}, r.run_id),
          el('span', { class: `badge b-${r.state === 'succeeded' ? 'ok' : r.state === 'failed' ? 'refused' : 'pending'}` }, r.state),
          el('span', { class: 'count' },
            `${r.songs.length} songs · ~$${r.estimate.usd} · ${pub} published`),
          ...chips, fin, actions),
        logBox);
    });
    host.replaceChildren(...rows);
    if (anchorId !== undefined && anchorTop !== undefined) {
      const replacement = [...host.querySelectorAll<HTMLElement>('.runlog:not([hidden])')]
        .find((box) => box.dataset.runId === anchorId);
      if (replacement) window.scrollBy(0, replacement.getBoundingClientRect().top - anchorTop);
    }
    await Promise.all([...host.querySelectorAll<HTMLElement>('.runlog:not([hidden])')]
      .map((box) => this.refreshLogs(box, box.dataset.runId!)));
  }
}
