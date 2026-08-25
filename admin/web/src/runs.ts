// Render-runs view: submit form (tree of canon-ok songs + tuning knobs, server-checked
// estimate) and the run list with live status, log tail, terminate/finish.
import { get, post } from './api';
import { el, note, statusLine } from './dom';

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

function input(value: string, attrs: Record<string, string> = {}): HTMLInputElement {
  return el('input', { value, ...attrs });
}

interface Capacity {
  lines?: string[];
  concurrent_shards: number | null;
  waves: number | null;
  planned_vcpus?: number;
  quota_vcpus?: number | null;
  ok: boolean;
  degraded: boolean;
  bad_pools?: string[];
  quota_code?: string;
}

/** One shard, folded server-side from its CloudWatch stream (renders.shard_states). */
interface ShardState {
  shard: number | null;
  stream: string | null;
  phase: string;
  songs: string[];
  done: number | null;
  total: number | null;
  failed: number | null;
  skipped: number | null;
  running: number | null;
  workers: number | null;
  eta_s: number | null;
  elapsed_s: number | null;
  staged_fonts: number | null;
  staged_gib: number | null;
  stage_s: number | null;
  render_rc: number | null;
  render_s: number | null;
  excluded: number | null;
  excluded_detail: string | null;
  published: { song: string; variants: number }[];
  problems: string[];
  phases: Record<string, number> | null;
  updated_at: number | null;
  last: string | null;
}

interface LogDoc {
  events: { t: number; msg: string; stream: string | null }[];
  shards: ShardState[];
  view: string;
  available: boolean;
}

function dur(s: number | null): string {
  if (s === null || !isFinite(s)) return '?';
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(Math.round(s % 60)).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

export class RunsView {
  root = el('div', { class: 'runs' });
  private songs: RenderSong[] = [];
  private renderEnabled = false;
  private missingCanon = 0;
  private picked = new Set<string>();
  private status = statusLine();
  private timer: number | null = null;
  private pollGen = 0; // bumped by stop(): a tick already in flight must not re-arm
  private openLogs = new Set<string>();
  // The panel node per run is kept ACROSS renders. renderRuns() replaces the whole list every
  // 5 s, and a log box rebuilt from scratch each time is empty until its own fetch returns —
  // which is the "appears and collapses" the box did on every poll. Reusing the node means the
  // panel keeps its content and its height while the next fetch is in flight.
  private logPanels = new Map<string, HTMLElement>();
  // which shard's unfiltered stream the run is showing, if any (null = the folded view)
  private rawStream = new Map<string, string | null>();
  private lastBody: Record<string, unknown> | null = null;

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
      const list = this.root.querySelector<HTMLElement>('.runlist');
      if (list) await this.refreshRuns(list);
      // stop() (tab switch) or a fresh load() may have landed while renderRuns was in
      // flight; clearTimeout alone cannot catch that, so re-arm only for our generation
      if (gen === this.pollGen) this.poll();
    }, 5000);
  }

  /** renderRuns() with the layout work it ends with (scroll anchoring) reported
   *  on the status line instead of thrown. renderRuns catches its own GET but not that, and
   *  every caller is a timer callback or a click handler: a rejection there is unhandled —
   *  it would end the poll chain for the rest of the session, or leave a click's own
   *  "done" on screen over a list that never refreshed, either way with nothing said. */
  private async refreshRuns(host: HTMLElement): Promise<void> {
    try {
      await this.renderRuns(host);
    } catch (e) {
      note(this.status, `runs refresh: ${(e as Error).message}`, true);
    }
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

  /** What the account can actually run under the knobs as typed (#25/#42). Advisory: a
   *  preflight that cannot answer must never stop someone estimating or submitting. */
  private async showCapacity(host: HTMLElement): Promise<void> {
    host.replaceChildren(el('span', { class: 'muted small' }, 'checking fleet capacity…'));
    let cap: Capacity;
    try {
      cap = await post<Capacity>('/api/render/capacity', this.capacityBody());
    } catch (e) {
      host.replaceChildren(el('span', { class: 'muted small' },
        `fleet capacity unknown: ${(e as Error).message}`));
      return;
    }
    const bad = !cap.ok || cap.degraded || (cap.bad_pools?.length ?? 0) > 0;
    const rows = [...(cap.lines ?? []), ...(cap.bad_pools ?? []).map((p) => `pool never launches: ${p}`)]
      .map((line) => el('div', {}, line));
    if (!cap.ok) {
      rows.push(el('div', { class: 'warn' },
        `raise the quota: aws service-quotas request-service-quota-increase --service-code ec2`
        + ` --quota-code ${cap.quota_code} --desired-value ${cap.planned_vcpus ?? ''}`));
    }
    host.replaceChildren(el('div', { class: `capbox${bad ? ' warn' : ''}` }, ...rows));
  }

  private capacityBody(): Record<string, unknown> {
    const b = this.lastBody ?? {};
    return {
      shards: b.shards, shard_vcpus: b.shard_vcpus,
      shard_memory_mib: b.shard_memory_mib, instance_types: b.instance_types,
    };
  }

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
    const estOut = el('span', { class: 'count estimate-out' });
    // the fleet-capacity answer sits with the estimate: both are "before you spend money"
    const capOut = el('div', { class: 'capacity' });

    const body = () => (this.lastBody = {
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
        note(this.status, (e as Error).message, true);
      }
      await this.showCapacity(capOut);
    };
    const submit = el('button', { class: 'danger' }, 'Submit to fleet');
    submit.onclick = async () => {
      if (!this.picked.size) return note(this.status, 'no songs selected', true);
      if (!confirm(`Submit ${this.picked.size} song(s) to the burst fleet? This spends real money.`)) return;
      submit.disabled = true;
      try {
        const run = await post<Run>('/api/runs', body());
        note(this.status, `submitted ${run.run_id}`);
        await this.renderRuns(this.root.querySelector('.runlist') as HTMLElement);
      } catch (e) {
        note(this.status, (e as Error).message, true);
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
        estimate, submit, count, estOut), capOut);
  }

  // ---------------------------------------------------------------- run list

  private logPanel(rid: string): HTMLElement {
    let panel = this.logPanels.get(rid);
    if (!panel) {
      panel = el('div', { class: 'runlog', 'data-run-id': rid, hidden: '' });
      this.logPanels.set(rid, panel);
    }
    return panel;
  }

  private async refreshLogs(rid: string): Promise<void> {
    const panel = this.logPanels.get(rid);
    if (!panel || panel.hidden || !panel.isConnected) return;
    const raw = this.rawStream.get(rid) ?? null;
    const qs = raw ? `?view=raw&stream=${encodeURIComponent(raw)}` : '';
    let doc: LogDoc;
    try {
      doc = await get<LogDoc>(`/api/runs/${rid}/logs${qs}`);
    } catch (e) {
      panel.replaceChildren(el('div', { class: 'logwait bad' },
        `logs unavailable: ${(e as Error).message}`));
      return;
    }
    if (panel.hidden || !panel.isConnected) return;   // toggled shut mid-flight
    panel.replaceChildren(...this.logChildren(rid, doc));
  }

  /** The panel body: a per-shard status board, or one shard's unfiltered tail. */
  private logChildren(rid: string, doc: LogDoc): HTMLElement[] {
    if (!doc.available) {
      return [el('div', { class: 'logwait' }, 'no CloudWatch log group is configured for this box')];
    }
    if (doc.view === 'raw') {
      const back = el('button', { class: 'linky' }, '\u2190 all shards');
      back.onclick = async () => { this.rawStream.set(rid, null); await this.refreshLogs(rid); };
      const pre = el('pre', { class: 'rawtail' },
        doc.events.map((e) => e.msg).join('\n') || '(nothing in this stream yet)');
      // a raw tail is only useful read from the end
      queueMicrotask(() => { pre.scrollTop = pre.scrollHeight; });
      return [el('div', { class: 'loghead' }, back,
        el('span', { class: 'dimtext' }, `${doc.events.length} lines, unfiltered`)), pre];
    }
    if (!doc.shards.length) {
      return [el('div', { class: 'logwait' },
        'no shard output yet \u2014 the fleet logs nothing until the containers start')];
    }
    return [this.fleetSummary(doc.shards),
      el('div', { class: 'shardgrid' }, ...doc.shards.map((s) => this.shardRow(rid, s)))];
  }

  /** The one line that answers "how is the run going": slowest shard sets the finish. */
  private fleetSummary(shards: ShardState[]): HTMLElement {
    const done = shards.reduce((n, s) => n + (s.done ?? 0), 0);
    const total = shards.reduce((n, s) => n + (s.total ?? 0), 0);
    const failed = shards.reduce((n, s) => n + (s.failed ?? 0), 0);
    const running = shards.reduce((n, s) => n + (s.running ?? 0), 0);
    const live = shards.filter((s) => s.phase !== 'done');
    // the run ends when the LAST shard does, so the fleet eta is the max, never the mean
    const etas = live.map((s) => s.eta_s).filter((e): e is number => e !== null);
    const bits = [`${shards.length} shards`];
    if (total) bits.push(`${done}/${total} variants (${Math.round((done / total) * 100)}%)`);
    if (running) bits.push(`${running} workers busy`);
    if (failed) bits.push(`${failed} failed`);
    if (etas.length && live.length) bits.push(`slowest shard ~${dur(Math.max(...etas))}`);
    const problems = shards.reduce((n, s) => n + s.problems.length, 0);
    return el('div', { class: `loghead${problems ? ' bad' : ''}` },
      el('span', {}, bits.join(' \u00b7 ')),
      problems ? el('span', { class: 'chip c-failed' }, `${problems} problem(s)`) : el('span', {}));
  }

  private shardRow(rid: string, s: ShardState): HTMLElement {
    const pct = s.done !== null && s.total ? Math.min(100, (s.done / s.total) * 100) : 0;
    const facts: string[] = [];
    if (s.done !== null && s.total) facts.push(`${s.done}/${s.total}`);
    if (s.running !== null) facts.push(`${s.running}/${s.workers ?? '?'} busy`);
    if (s.failed) facts.push(`${s.failed} failed`);
    if (s.phase === 'rendering' && s.eta_s !== null) facts.push(`eta ${dur(s.eta_s)}`);
    if (s.phase === 'staging' || s.phase === 'starting') facts.push('staging fonts');
    if (s.stage_s !== null && s.phase !== 'staging') facts.push(`staged in ${dur(s.stage_s)}`);
    if (s.render_s !== null) facts.push(`rendered in ${dur(s.render_s)}`);
    if (s.published.length) facts.push(`${s.published.length} published`);
    // an outcome, not a fault: silent fonts and the #27 peak ceiling. Without it, "550 of 566
    // published" looks like something went wrong and nothing says what.
    if (s.excluded) facts.push(`${s.excluded} excluded`);
    const tail = el('div', { class: 'srow-detail' },
      el('span', { class: 'dimtext' },
        s.excluded_detail ? `excluded: ${s.excluded_detail}` : (s.songs.join(', ') || (s.last ?? ''))));
    if (s.stream) {
      const rawBtn = el('button', { class: 'linky' }, 'raw');
      rawBtn.onclick = async () => { this.rawStream.set(rid, s.stream); await this.refreshLogs(rid); };
      tail.append(rawBtn);
    }
    return el('div', { class: `srow ph-${s.phase}` },
      el('div', { class: 'srow-head' },
        el('strong', {}, s.shard === null ? 'shard ?' : `shard ${s.shard}`),
        el('span', { class: `sphase ph-${s.phase}` }, s.phase),
        el('span', { class: 'dimtext' }, facts.join(' \u00b7 '))),
      el('div', { class: 'sbar' },
        el('div', { class: `sfill ph-${s.phase}`, style: `width:${pct.toFixed(1)}%` })),
      tail,
      ...s.problems.map((t) => el('div', { class: 'sproblem' }, t)));
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
          t.disabled = true;
          try {
            await post(`/api/runs/${r.run_id}/terminate`);
          } catch (e) {
            note(this.status, `terminate: ${(e as Error).message}`, true);
          } finally {
            t.disabled = false;
          }
          await this.refreshRuns(host);
        };
        actions.append(t);
      } else if (!r.finisher.songs_json_published) {
        const f = el('button', {}, 'Run finisher');
        f.onclick = async () => {
          // /finish 409s only when this run is ALREADY being finished (the watcher's own
          // finisher, or a second click): RunManager.finish() catches the publish mutex
          // being held and records it on the record it returns, so the request succeeds
          // while the finisher published nothing. Both have to be shown — unhandled, the
          // rejection left the status line stuck on "finisher running…", and a plain
          // "finisher done" claimed a rebuild that did not happen (#19).
          f.disabled = true;
          note(this.status, 'finisher running…');
          try {
            const rec = await post<Run>(`/api/runs/${r.run_id}/finish`);
            if (rec.finisher.songs_json_published) note(this.status, 'finisher done');
            else note(this.status, `finisher: ${rec.finisher.error ?? 'songs.json was not published'}`, true);
          } catch (e) {
            note(this.status, `finisher: ${(e as Error).message}`, true);
          } finally {
            f.disabled = false;
          }
          await this.refreshRuns(host);
        };
        actions.append(f);
      }
      const logIsOpen = this.openLogs.has(r.run_id);
      const logs = el('button', {}, logIsOpen ? 'Hide logs' : 'Logs');
      const logBox = this.logPanel(r.run_id);   // the SAME node every render — see logPanels
      logBox.hidden = !logIsOpen;
      logs.onclick = async () => {
        if (this.openLogs.has(r.run_id)) {
          this.openLogs.delete(r.run_id);
          logBox.hidden = true;
          logs.textContent = 'Logs';
          return;
        }
        this.openLogs.add(r.run_id);
        logBox.hidden = false;
        logs.textContent = 'Hide logs';
        if (!logBox.firstChild) logBox.replaceChildren(el('div', { class: 'logwait' }, 'loading…'));
        await this.refreshLogs(r.run_id);
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
    // a run that has dropped off the list keeps neither a panel nor a poll
    const listed = new Set(runs.map((r) => r.run_id));
    for (const rid of [...this.logPanels.keys()]) {
      if (!listed.has(rid)) { this.logPanels.delete(rid); this.openLogs.delete(rid); this.rawStream.delete(rid); }
    }
    await Promise.all([...this.openLogs].filter((rid) => listed.has(rid))
      .map((rid) => this.refreshLogs(rid)));
  }
}
