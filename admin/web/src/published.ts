// Published view: what the site bucket actually serves (live songs.json ⨝ S3 listings),
// per-track removal, and S3-listing-based prune with a mandatory dry-run first.
import { del, get, post } from './api';

interface Track {
  id: string;
  title: string | null;
  path: string | null;
  variant_count: number | null;
  duration_s: number | null;
  audio_objects: number;
  audio_bytes: number;
  set_docs: number;
  in_songs_json: boolean;
}

interface Overview {
  generated_at: string | null;
  tracks: Track[];
  discrepancies: string[];
}

interface PruneReport {
  dry_run: boolean;
  kept_songs: number;
  doomed_objects: number;
  doomed_by_prefix: Record<string, number>;
  deleted?: number;
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

const fmtMB = (n: number) => `${(n / (1 << 20)).toFixed(1)} MB`;

export class PublishedView {
  root = el('div', { class: 'published' });
  private status = el('span', { class: 'statusline' });
  private lastPrune: PruneReport | null = null;

  async load(): Promise<void> {
    this.root.replaceChildren(el('div', { class: 'notice' }, 'listing the site bucket…'));
    let ov: Overview;
    try {
      ov = await get<Overview>('/api/published');
    } catch (e) {
      this.root.replaceChildren(el('div', { class: 'notice' }, (e as Error).message));
      return;
    }
    this.render(ov);
  }

  private note(msg: string, isError = false): void {
    this.status.textContent = msg;
    this.status.classList.toggle('error', isError);
  }

  private render(ov: Overview): void {
    const pruneDry = el('button', {}, 'Prune (dry run)');
    const pruneReal = el('button', { class: 'danger', disabled: '' }, 'Prune: delete');
    const pruneOut = el('pre', { class: 'logbox', hidden: '' });
    pruneDry.onclick = async () => {
      this.note('prune dry run…');
      try {
        this.lastPrune = await post<PruneReport>('/api/published/prune', { dry_run: true });
        pruneOut.hidden = false;
        pruneOut.textContent = JSON.stringify(this.lastPrune, null, 1);
        pruneReal.disabled = this.lastPrune.doomed_objects === 0;
        this.note(`${this.lastPrune.doomed_objects} objects unreferenced`);
      } catch (e) {
        this.note((e as Error).message, true);
      }
    };
    pruneReal.onclick = async () => {
      const n = this.lastPrune?.doomed_objects ?? 0;
      if (!confirm(`Permanently delete ${n} unreferenced objects from the site bucket?`)) return;
      this.note('pruning…');
      try {
        const r = await post<PruneReport>('/api/published/prune', { dry_run: false });
        pruneOut.textContent = JSON.stringify(r, null, 1);
        this.note(`deleted ${r.deleted ?? 0} objects`);
        pruneReal.disabled = true;
      } catch (e) {
        this.note((e as Error).message, true);
      }
    };
    const rebuild = el('button', {}, 'Republish songs.json');
    rebuild.onclick = async () => {
      // minutes-long request: without this a double-click (or an impatient second click)
      // fired two rebuilds, and the server has to refuse one of them with a 409 (#19)
      rebuild.disabled = true;
      this.note('rebuilding songs.json…');
      try {
        await post('/api/published/rebuild');
        this.note('songs.json republished');
        await this.load();
      } catch (e) {
        this.note((e as Error).message, true);
      } finally {
        rebuild.disabled = false;
      }
    };

    const rows = ov.tracks.map((t) => {
      const bad = ov.discrepancies.includes(t.id);
      const rm = el('button', { class: 'danger' }, 'Remove');
      rm.onclick = async () => {
        if (!confirm(`Remove ${t.id} from the site? Deletes ${t.audio_objects} audio objects `
          + `(${fmtMB(t.audio_bytes)}) + set docs and republishes songs.json. Renders are gone for good.`)) return;
        this.note(`removing ${t.id}…`);
        try {
          await del(`/api/published/${t.id}`);
          this.note(`${t.id} removed`);
          await this.load();
        } catch (e) {
          this.note((e as Error).message, true);
        }
      };
      return el('tr', { class: bad ? 'bad' : '' },
        el('td', {}, t.title ?? el('em', {}, '(not in songs.json)')),
        el('td', { class: 'count' }, t.path ?? ''),
        el('td', { class: 'count' }, t.id),
        el('td', {}, t.variant_count != null ? String(t.variant_count) : '—'),
        el('td', {}, String(t.audio_objects)),
        el('td', {}, fmtMB(t.audio_bytes)),
        el('td', {}, String(t.set_docs)),
        el('td', {}, bad ? el('span', { class: 'chip c-failed' }, 'discrepancy') : ''),
        el('td', {}, rm));
    });

    this.root.replaceChildren(
      el('div', { class: 'toolbar' },
        el('h2', {}, `Published (${ov.tracks.filter((t) => t.in_songs_json).length} live tracks)`),
        rebuild, pruneDry, pruneReal, this.status),
      pruneOut,
      el('table', { class: 'pubtable' },
        el('thead', {}, el('tr', {},
          ...['title', 'path', 'id', 'variants', 'audio objs', 'audio size', 'sets', '', '']
            .map((h) => el('th', {}, h)))),
        el('tbody', {}, ...rows)));
  }
}
