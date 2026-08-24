// Library view: folder tree + details panel (preview, metadata, move, inject quick-fix).
import { del, get, patch, post } from './api';

export interface CanonInfo {
  status: 'ok' | 'pending' | 'refused' | 'unparsed';
  reason: string | null;
  canonical_sha256: string | null;
  duration_s: number | null;
  checked_at: string | null;
}

export interface Entry {
  id: string;
  path: string;
  name: string;
  sha256: string;
  size: number;
  composer: string | null;
  sequencer: string | null;
  source_url: string | null;
  hidden: boolean;
  inject: unknown[] | null;
  trim: unknown | null;
  notes: string | null;
  canon: CanonInfo;
}

export interface LibraryDoc {
  updated_at: string | null;
  entries: Entry[];
  preview: { fluidsynth: boolean; ffmpeg: boolean; gm_sf2: boolean };
}

interface ChannelInfo {
  channel: number;
  track: number;
  notes: number;
  missing_program: boolean;
}

const OPEN_KEY = 'sfadmin.folders.v1';

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

function fmtSize(n: number): string {
  return n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function fmtDur(s: number | null): string {
  if (s == null) return '';
  return `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
}

export class LibraryView {
  root = el('div', { class: 'library' });
  private doc: LibraryDoc | null = null;
  private selected: string | null = null;
  private filter = '';
  private open = new Set<string>();
  private status = el('span', { class: 'statusline' });

  constructor() {
    try {
      this.open = new Set(JSON.parse(localStorage.getItem(OPEN_KEY) ?? '[]'));
    } catch { /* fresh */ }
  }

  async load(): Promise<void> {
    this.doc = await get<LibraryDoc>('/api/library');
    this.render();
  }

  private saveOpen(): void {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify([...this.open]));
    } catch { /* private mode */ }
  }

  private note(msg: string, isError = false): void {
    this.status.textContent = msg;
    this.status.classList.toggle('error', isError);
  }

  private async act(label: string, fn: () => Promise<unknown>): Promise<boolean> {
    try {
      this.note(`${label}…`);
      await fn();
      await this.load();
      this.note(`${label}: done`);
      return true;
    } catch (e) {
      this.note(`${label}: ${(e as Error).message}`, true);
      return false;
    }
  }

  // ---------------------------------------------------------------- tree

  private render(): void {
    if (!this.doc) return;
    const entries = this.doc.entries.filter((e) => {
      if (!this.filter) return true;
      const f = this.filter.toLowerCase();
      return e.path.toLowerCase().includes(f) || e.name.toLowerCase().includes(f)
        || (e.composer ?? '').toLowerCase().includes(f);
    });

    const byDir = new Map<string, Entry[]>();
    for (const e of entries) {
      const dir = e.path.includes('/') ? e.path.slice(0, e.path.lastIndexOf('/')) : '';
      (byDir.get(dir) ?? byDir.set(dir, []).get(dir)!).push(e);
    }
    const dirs = [...byDir.keys()].sort();

    const refused = this.doc.entries.filter((e) => e.canon.status === 'refused' || e.canon.status === 'unparsed');
    const tree = el('div', { class: 'tree' });
    for (const dir of dirs) {
      const label = dir === '' ? '(top level)' : dir;
      const files = byDir.get(dir)!;
      const isOpen = this.filter !== '' || this.open.has(dir);
      const head = el('div', { class: 'folder' },
        el('span', { class: 'twist' }, isOpen ? '▾' : '▸'),
        el('span', { class: 'fname' }, label),
        el('span', { class: 'count' }, String(files.length)));
      head.onclick = () => {
        if (this.open.has(dir)) this.open.delete(dir);
        else this.open.add(dir);
        this.saveOpen();
        this.render();
      };
      tree.append(head);
      if (!isOpen) continue;
      for (const e of files) {
        const row = el('div', { class: `row st-${e.canon.status}${e.hidden ? ' hid' : ''}${e.id === this.selected ? ' sel' : ''}` },
          el('span', { class: 'nm', title: e.path }, e.name),
          el('span', { class: 'meta' },
            `${fmtDur(e.canon.duration_s)} ${fmtSize(e.size)} ${e.composer ?? ''}`),
          el('span', { class: `badge b-${e.canon.status}` }, e.hidden ? 'hidden' : e.canon.status));
        row.onclick = () => {
          this.selected = e.id;
          this.render();
        };
        tree.append(row);
      }
    }

    const toolbar = this.toolbar(refused.length);
    const details = this.selected ? this.details(this.doc.entries.find((e) => e.id === this.selected)) : null;
    this.root.replaceChildren(toolbar,
      el('div', { class: 'cols' }, tree, details ?? el('div', { class: 'detail empty' }, 'select a track')));
  }

  private toolbar(refusedCount: number): HTMLElement {
    const search = el('input', { type: 'search', placeholder: 'filter…', value: this.filter });
    search.oninput = () => {
      this.filter = search.value;
      this.render();
    };
    const upload = el('button', {}, 'Upload…');
    upload.onclick = () => this.uploadDialog();
    const zip = el('a', { href: '/api/library.zip', class: 'btnish' }, 'Download all');
    const canon = el('button', {}, refusedCount ? `Canon check (${refusedCount} refused)` : 'Canon check');
    canon.onclick = () => this.canonRun();
    const n = this.doc!.entries.length;
    const pv = this.doc!.preview;
    const pvWarn = pv.gm_sf2 ? '' : ' — no gm.sf2, previews off';
    return el('div', { class: 'toolbar' },
      search, upload, canon, zip,
      el('span', { class: 'count' }, `${n} tracks${pvWarn}`),
      this.status);
  }

  // ---------------------------------------------------------------- details

  private details(e: Entry | undefined): HTMLElement {
    if (!e) return el('div', { class: 'detail empty' }, 'gone');
    const d = el('div', { class: 'detail' });
    d.append(el('h2', {}, e.name), el('div', { class: 'path' }, e.path));

    const audio = el('audio', { controls: '', preload: 'none', src: `/api/preview/${e.id}.mp3` });
    d.append(audio);

    // metadata form
    const fields: [keyof Entry, string][] = [
      ['name', 'display name'], ['composer', 'composer'], ['sequencer', 'sequencer'],
      ['source_url', 'source url'], ['notes', 'notes'],
    ];
    const inputs = new Map<string, HTMLInputElement>();
    const form = el('div', { class: 'form' });
    for (const [key, label] of fields) {
      const inp = el('input', { value: (e[key] as string | null) ?? '' });
      inputs.set(key, inp);
      form.append(el('label', {}, label, inp));
    }
    const save = el('button', {}, 'Save metadata');
    save.onclick = async () => {
      const body: Record<string, string | null> = {};
      for (const [k, inp] of inputs) body[k] = inp.value.trim() || null;
      if (!body.name) body.name = e.name;
      await this.act('save', () => patch(`/api/library/${e.id}`, body));
    };
    form.append(save);
    d.append(form);

    // move / rename (path)
    const pathInp = el('input', { value: e.path });
    const move = el('button', {}, 'Move / rename file');
    move.onclick = () => this.act('move', () => post(`/api/library/${e.id}/move`, { path: pathInp.value.trim() }));
    d.append(el('div', { class: 'form' }, el('label', {}, 'file path', pathInp), move));

    // hide / delete
    const hide = el('button', {}, e.hidden ? 'Unhide (show on site)' : 'Hide from site');
    hide.onclick = () => this.act(e.hidden ? 'unhide' : 'hide',
      () => patch(`/api/library/${e.id}`, { hidden: !e.hidden }));
    const rm = el('button', { class: 'danger' }, 'Delete from library');
    rm.onclick = async () => {
      if (!confirm(`Delete ${e.path} from the library (S3 + mirror)? Published renders are cleaned up separately.`)) return;
      this.selected = null;
      await this.act('delete', () => del(`/api/library/${e.id}`));
    };
    d.append(el('div', { class: 'btnrow' }, hide, rm));

    // canon status + quick fix
    const c = e.canon;
    const canonBox = el('div', { class: `canonbox b-${c.status}` },
      el('strong', {}, `canon: ${c.status}`),
      c.reason ? el('div', { class: 'reason' }, c.reason) : '');
    if (c.status === 'refused' && (c.reason ?? '').includes('program change')) {
      const fix = el('button', {}, 'Fix missing programs…');
      fix.onclick = () => this.injectFix(e);
      canonBox.append(fix);
    }
    const recheck = el('button', {}, 'Re-run canon for this track');
    recheck.onclick = () => this.canonRun([e.id]);
    canonBox.append(recheck);
    d.append(canonBox);
    d.append(el('div', { class: 'ids' }, `id: ${e.id} · sha256: ${e.sha256.slice(0, 12)}…`));
    return d;
  }

  // ---------------------------------------------------------------- actions

  private uploadDialog(): void {
    const inp = el('input', { type: 'file', multiple: '', accept: '.mid,.midi,.rmi' });
    inp.onchange = async () => {
      if (!inp.files?.length) return;
      const dir = prompt('Upload into directory (empty = top level):',
        this.selectedDir()) ?? '';
      const fd = new FormData();
      for (const f of inp.files) fd.append('files', f);
      fd.append('dir', dir.trim());
      await this.act(`upload ${inp.files.length} file(s)`, async () => {
        const r = await fetch('/api/library/upload', { method: 'POST', body: fd });
        if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
        const res = await r.json() as { results: { ok: boolean; path: string; error?: string }[] };
        const bad = res.results.filter((x) => !x.ok);
        if (bad.length) throw new Error(`${bad.length} failed: ${bad[0]!.path}: ${bad[0]!.error}`);
      });
    };
    inp.click();
  }

  private selectedDir(): string {
    if (!this.selected || !this.doc) return '';
    const e = this.doc.entries.find((x) => x.id === this.selected);
    return e && e.path.includes('/') ? e.path.slice(0, e.path.lastIndexOf('/')) : '';
  }

  private async canonRun(ids?: string[]): Promise<void> {
    try {
      this.note('canon: starting…');
      await post('/api/library/canon', ids ? { ids } : {});
    } catch (e) {
      this.note(`canon: ${(e as Error).message}`, true);
      return;
    }
    const poll = async (): Promise<void> => {
      const s = await get<{ running: boolean; error?: string; result?: Record<string, number> }>('/api/library/canon/status');
      if (s.running) {
        this.note('canon: running… (a full run takes a few minutes on this box)');
        setTimeout(poll, 3000);
        return;
      }
      if (s.error) this.note(`canon: ${s.error}`, true);
      else this.note(`canon: ${JSON.stringify(s.result)}`);
      await this.load();
    };
    poll();
  }

  private async injectFix(e: Entry): Promise<void> {
    let chans: ChannelInfo[];
    try {
      chans = await get<ChannelInfo[]>(`/api/library/${e.id}/channels`);
    } catch (err) {
      this.note(`channels: ${(err as Error).message}`, true);
      return;
    }
    const missing = chans.filter((c) => c.missing_program);
    const rules: { track: number; channel: number; program: number }[] = [];
    for (const c of missing) {
      const dflt = c.channel === 10 ? '0' : '';
      const p = prompt(
        `Channel ${c.channel} (track ${c.track}, ${c.notes} notes) has no program change.\n`
        + 'GM program number 0–127 (ch10: 0 = standard drum kit); empty to skip:', dflt);
      if (p === null) return;
      if (p.trim() === '') continue;
      const num = Number(p);
      if (!Number.isInteger(num) || num < 0 || num > 127) {
        this.note(`bad program ${p}`, true);
        return;
      }
      rules.push({ track: c.track, channel: c.channel, program: num });
    }
    if (!rules.length) return;
    const merged = [...(e.inject as typeof rules | null ?? []), ...rules];
    const ok = await this.act('inject', () => patch(`/api/library/${e.id}`, { inject: merged }));
    if (ok) await this.canonRun([e.id]);
  }
}
