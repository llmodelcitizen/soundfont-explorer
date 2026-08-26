// Published view: what the site bucket actually serves (live songs.json ⨝ S3 listings),
// per-track removal, and S3-listing-based prune with a mandatory dry-run first.
//
// The overview says which songs HAVE audio, never how much (#46): counting bytes meant the
// server walking every object under a/ — ~2.4M of them — so this tab took minutes and in
// practice never finished, which also put Republish songs.json out of reach. A song's real
// weight is listed one song at a time, when someone asks for it: see the audio column and
// the Remove confirmation, both of which quote a figure measured seconds earlier rather
// than a remembered one that would have to be captioned with its age to be honest.
import { del, get, isSessionExpired, post } from './api';
import { el, note, statusLine } from './dom';

interface Track {
  id: string;
  title: string | null;
  path: string | null;
  variant_count: number | null;
  duration_s: number | null;
  /** Whether a/<id>/ exists at all — a delimited listing, not a count. */
  has_audio: boolean;
  set_docs: number;
  in_songs_json: boolean;
}

interface Overview {
  generated_at: string | null;
  tracks: Track[];
  discrepancies: string[];
}

/** GET /api/published/<id>/audio: one song's prefix, listed exactly, at `measured_at`. */
interface AudioUsage {
  id: string;
  objects: number;
  bytes: number;
  measured_at: string;
}

interface PruneReport {
  dry_run: boolean;
  kept_songs: number;
  /** Set docs songs.json names that the bucket no longer has: those tracks are kept whole. */
  missing_sets: string[];
  doomed_objects: number;
  doomed_by_prefix: Record<string, number>;
  deleted?: number;
}

// A bucket whose only anomaly is a missing set doc prunes 0 objects, so say it in the
// status line — otherwise the gap is visible only in the raw JSON below.
const gaps = (r: PruneReport) =>
  r.missing_sets?.length ? `, ${r.missing_sets.length} set docs missing` : '';

const fmtMB = (n: number) => `${(n / (1 << 20)).toFixed(1)} MB`;

const HEADERS: [string, string][] = [
  ['title', ''], ['path', ''], ['id', ''], ['variants', ''],
  ['audio', 'objects under a/<id>/ — the overview only checks that the prefix exists; '
    + 'measure lists that one song'],
  ['sets', ''], ['', ''], ['', ''],
];

export class PublishedView {
  root = el('div', { class: 'published' });
  private status = statusLine();
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

  /** One song's object count + size, or null if the listing failed (reported on the status
   *  line, unless the session expired — the page is already leaving for /auth/login). */
  private async measure(sid: string): Promise<AudioUsage | null> {
    try {
      return await get<AudioUsage>(`/api/published/${sid}/audio`);
    } catch (e) {
      if (!isSessionExpired(e)) note(this.status, (e as Error).message, true);
      return null;
    }
  }

  /** The audio cell: an offer to go and list the song, replaced by what came back. Nothing
   *  is shown before that — a number nobody measured, or one measured minutes ago and shown
   *  bare, would read as the live figure it is not. */
  private audioCell(t: Track): HTMLElement {
    const td = el('td', {});
    if (!t.has_audio) {
      td.append(el('span', { class: 'count', title: `nothing under a/${t.id}/` }, 'none'));
      return td;
    }
    const measure = el('button', {}, 'measure');
    measure.onclick = async () => {
      measure.disabled = true;
      measure.textContent = 'listing…';
      const u = await this.measure(t.id);
      if (!u) {
        measure.disabled = false;
        measure.textContent = 'measure';
        return;
      }
      // stamped: a live run publishes more variants under this very prefix as we look at it
      td.replaceChildren(el('span', { title: `listed at ${u.measured_at}` },
        `${u.objects} objs · ${fmtMB(u.bytes)}`));
    };
    td.append(measure);
    return td;
  }

  private render(ov: Overview): void {
    const pruneDry = el('button', {}, 'Prune (dry run)');
    const pruneReal = el('button', { class: 'danger', disabled: '' }, 'Prune: delete');
    const pruneOut = el('pre', { class: 'logbox', hidden: '' });
    pruneDry.onclick = async () => {
      note(this.status, 'prune dry run…');
      try {
        this.lastPrune = await post<PruneReport>('/api/published/prune', { dry_run: true });
        pruneOut.hidden = false;
        pruneOut.textContent = JSON.stringify(this.lastPrune, null, 1);
        pruneReal.disabled = this.lastPrune.doomed_objects === 0;
        note(this.status, `${this.lastPrune.doomed_objects} objects unreferenced${gaps(this.lastPrune)}`);
      } catch (e) {
        note(this.status, (e as Error).message, true);
      }
    };
    pruneReal.onclick = async () => {
      const n = this.lastPrune?.doomed_objects ?? 0;
      if (!confirm(`Permanently delete ${n} unreferenced objects from the site bucket?`)) return;
      note(this.status, 'pruning…');
      try {
        const r = await post<PruneReport>('/api/published/prune', { dry_run: false });
        pruneOut.textContent = JSON.stringify(r, null, 1);
        note(this.status, `deleted ${r.deleted ?? 0} objects${gaps(r)}`);
        pruneReal.disabled = true;
      } catch (e) {
        note(this.status, (e as Error).message, true);
      }
    };
    const rebuild = el('button', {}, 'Republish songs.json');
    rebuild.onclick = async () => {
      // minutes-long request: without this a double-click (or an impatient second click)
      // fired two rebuilds, and the server has to refuse one of them with a 409 (#19)
      rebuild.disabled = true;
      note(this.status, 'rebuilding songs.json…');
      try {
        await post('/api/published/rebuild');
        note(this.status, 'songs.json republished');
        await this.load();
      } catch (e) {
        note(this.status, (e as Error).message, true);
      } finally {
        rebuild.disabled = false;
      }
    };

    const rows = ov.tracks.map((t) => {
      const bad = ov.discrepancies.includes(t.id);
      const rm = el('button', { class: 'danger' }, 'Remove');
      rm.onclick = async () => {
        // The overview carries no counts any more (#46) and this destroys renders for good,
        // so list the track's audio now: the confirmation names what was there a second ago
        // instead of a number of unknown age. A failed listing must not block the removal —
        // the wording just goes vague, and measure() has already said why on the status line.
        rm.disabled = true;
        note(this.status, `listing ${t.id}'s audio…`);
        const u = await this.measure(t.id);
        rm.disabled = false;
        const cost = u ? `${u.objects} audio objects (${fmtMB(u.bytes)})` : 'its audio objects';
        if (!confirm(`Remove ${t.id} from the site? Deletes ${cost} `
          + '+ set docs and republishes songs.json. Renders are gone for good.')) {
          if (u) note(this.status, ''); // drop the "listing…" note, but not a failure's reason
          return;
        }
        note(this.status, `removing ${t.id}…`);
        try {
          await del(`/api/published/${t.id}`);
          note(this.status, `${t.id} removed`);
          await this.load();
        } catch (e) {
          note(this.status, (e as Error).message, true);
        }
      };
      return el('tr', { class: bad ? 'bad' : '' },
        el('td', {}, t.title ?? el('em', {}, '(not in songs.json)')),
        el('td', { class: 'count' }, t.path ?? ''),
        el('td', { class: 'count' }, t.id),
        el('td', {}, t.variant_count != null ? String(t.variant_count) : '—'),
        this.audioCell(t),
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
          ...HEADERS.map(([h, tip]) => el('th', tip ? { title: tip } : {}, h)))),
        el('tbody', {}, ...rows)));
  }
}
