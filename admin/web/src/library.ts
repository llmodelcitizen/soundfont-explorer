// Library view: folder tree + details panel (preview, metadata, move, inject quick-fix),
// keyboard-driven. Selection supports shift (range) and cmd/ctrl (toggle) multi-select;
// every single-track action is available as a bulk action (one server call, one
// library.json save). Keys: ↑/↓ move, ←/→ fold/unfold, space play/pause, c canon,
// h hide/unhide, d delete. "Play on click" auto-previews the selected track, debounced
// 350 ms so arrowing through the list doesn't stack renders on the 2-vCPU box (the
// server additionally caps concurrent preview renders at 2).
import { get, patch, post } from './api';

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

type Row = { kind: 'folder'; path: string } | { kind: 'track'; e: Entry };

const OPEN_KEY = 'sfadmin.folders.v1';
const AUTOPLAY_KEY = 'sfadmin.autoplay.v1';

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
  private rows: Row[] = [];
  private selected = new Set<string>();
  private anchor: string | null = null;
  private cursor = -1;
  private keyboardNav = false;
  private filter = '';
  private open = new Set<string>();
  private autoplay = false;
  private autoplayTimer: number | null = null;
  private status = el('span', { class: 'statusline' });
  private audio = el('audio', { controls: '', preload: 'none' });
  private audioFor: string | null = null;

  constructor() {
    try {
      this.open = new Set(JSON.parse(localStorage.getItem(OPEN_KEY) ?? '[]'));
      this.autoplay = localStorage.getItem(AUTOPLAY_KEY) === '1';
    } catch { /* fresh */ }
    document.addEventListener('keydown', (ev) => this.onKey(ev));
  }

  async load(): Promise<void> {
    this.doc = await get<LibraryDoc>('/api/library');
    const ids = new Set(this.doc.entries.map((e) => e.id));
    this.selected = new Set([...this.selected].filter((i) => ids.has(i)));
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

  // ---------------------------------------------------------------- selection + keys

  private selectOnly(id: string, rowIndex: number, play = true): void {
    this.selected = new Set([id]);
    this.anchor = id;
    this.cursor = rowIndex;
    this.render();
    if (play) this.maybeAutoplay();
  }

  private trackRowIndexes(): number[] {
    return this.rows.flatMap((r, i) => (r.kind === 'track' ? [i] : []));
  }

  private onTrackClick(ev: MouseEvent, e: Entry, rowIndex: number): void {
    if (ev.shiftKey && this.anchor) {
      const tracks = this.trackRowIndexes();
      const ai = tracks.findIndex((i) => (this.rows[i] as { e: Entry }).e.id === this.anchor);
      const bi = tracks.indexOf(rowIndex);
      if (ai >= 0 && bi >= 0) {
        const [lo, hi] = ai < bi ? [ai, bi] : [bi, ai];
        this.selected = new Set(tracks.slice(lo, hi + 1)
          .map((i) => (this.rows[i] as { e: Entry }).e.id));
        this.cursor = rowIndex;
        this.render();
        return;
      }
    }
    if (ev.metaKey || ev.ctrlKey) {
      if (this.selected.has(e.id)) this.selected.delete(e.id);
      else this.selected.add(e.id);
      this.anchor = e.id;
      this.cursor = rowIndex;
      this.render();
      return;
    }
    this.selectOnly(e.id, rowIndex);
  }

  private onKey(ev: KeyboardEvent): void {
    if (!this.root.isConnected) return; // another tab is showing
    const t = ev.target as HTMLElement;
    if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable) return;
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    switch (ev.key) {
      case 'ArrowDown':
      case 'ArrowUp': {
        ev.preventDefault();
        this.keyboardNav = true;
        const dir = ev.key === 'ArrowDown' ? 1 : -1;
        const next = Math.min(this.rows.length - 1, Math.max(0, this.cursor + dir));
        if (next === this.cursor) return;
        const row = this.rows[next]!;
        if (row.kind === 'track') this.selectOnly(row.e.id, next);
        else {
          this.cursor = next;
          this.render();
        }
        break;
      }
      case 'ArrowRight': {
        const row = this.rows[this.cursor];
        if (row?.kind === 'folder') {
          ev.preventDefault();
          this.keyboardNav = true;
          if (!this.open.has(row.path)) {
            this.open.add(row.path);
            this.saveOpen();
            this.render();
          } else if (this.rows[this.cursor + 1]?.kind === 'track') {
            const nr = this.rows[this.cursor + 1] as { kind: 'track'; e: Entry };
            this.selectOnly(nr.e.id, this.cursor + 1);
          }
        }
        break;
      }
      case 'ArrowLeft': {
        const row = this.rows[this.cursor];
        ev.preventDefault();
        this.keyboardNav = true;
        if (row?.kind === 'folder' && this.open.has(row.path)) {
          this.open.delete(row.path);
          this.saveOpen();
          this.render();
        } else {
          // jump to (or collapse toward) the containing folder header
          for (let i = this.cursor - 1; i >= 0; i--) {
            if (this.rows[i]!.kind === 'folder') {
              this.cursor = i;
              this.render();
              break;
            }
          }
        }
        break;
      }
      case ' ': {
        ev.preventDefault();
        this.togglePlay();
        break;
      }
      case 'c': {
        if (this.selected.size) this.canonRun([...this.selected]);
        break;
      }
      case 'h': {
        if (this.selected.size) this.hideSelected();
        break;
      }
      case 'd': {
        if (this.selected.size) this.deleteSelected();
        break;
      }
    }
  }

  // ---------------------------------------------------------------- audio

  private setAudio(id: string): void {
    if (this.audioFor !== id) {
      this.audio.src = `/api/preview/${id}.mp3`;
      this.audioFor = id;
    }
  }

  private maybeAutoplay(): void {
    if (this.autoplayTimer !== null) clearTimeout(this.autoplayTimer);
    if (!this.autoplay || this.selected.size !== 1) return;
    const id = [...this.selected][0]!;
    // debounce: arrowing through tracks must not stack fluidsynth renders on the tiny box
    this.autoplayTimer = window.setTimeout(() => {
      this.setAudio(id);
      this.audio.play().catch(() => { /* autoplay policy or abort */ });
    }, 350);
  }

  private togglePlay(): void {
    if (this.selected.size === 1) this.setAudio([...this.selected][0]!);
    if (!this.audio.src) return;
    if (this.audio.paused) this.audio.play().catch(() => { /* not allowed */ });
    else this.audio.pause();
  }

  // ---------------------------------------------------------------- bulk ops

  private hideSelected(): void {
    const ids = [...this.selected];
    const entries = ids.map((i) => this.doc!.entries.find((e) => e.id === i)!);
    const hide = !entries.every((e) => e.hidden); // mixed or visible → hide; all hidden → unhide
    if (!confirm(`${hide ? 'Hide' : 'Unhide'} ${ids.length} track(s)?`)) return;
    void this.act(hide ? 'hide' : 'unhide',
      () => post('/api/library/bulk', { op: 'edit', ids, fields: { hidden: hide } }));
  }

  private deleteSelected(): void {
    const ids = [...this.selected];
    if (!confirm(`Delete ${ids.length} track(s) from the library (S3 + mirror)? `
      + 'Published renders are cleaned up separately on the Published tab.')) return;
    this.selected.clear();
    void this.act(`delete ${ids.length}`,
      () => post('/api/library/bulk', { op: 'delete', ids }));
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

    this.rows = [];
    const tree = el('div', { class: 'tree', tabindex: '0' });
    for (const dir of [...byDir.keys()].sort()) {
      const files = byDir.get(dir)!;
      const isOpen = this.filter !== '' || this.open.has(dir);
      const folderIndex = this.rows.length;
      this.rows.push({ kind: 'folder', path: dir });
      const head = el('div', { class: `folder${this.cursor === folderIndex ? ' cur' : ''}` },
        el('span', { class: 'twist' }, isOpen ? '▾' : '▸'),
        el('span', { class: 'fname' }, dir === '' ? '(top level)' : dir),
        el('span', { class: 'count' }, String(files.length)));
      head.onclick = () => {
        this.cursor = folderIndex;
        if (this.open.has(dir)) this.open.delete(dir);
        else this.open.add(dir);
        this.saveOpen();
        this.render();
      };
      tree.append(head);
      if (!isOpen) continue;
      for (const e of files) {
        const rowIndex = this.rows.length;
        this.rows.push({ kind: 'track', e });
        const cls = `row st-${e.canon.status}${e.hidden ? ' hid' : ''}`
          + `${this.selected.has(e.id) ? ' sel' : ''}${this.cursor === rowIndex ? ' cur' : ''}`;
        const row = el('div', { class: cls },
          el('span', { class: 'nm', title: e.path }, e.name),
          el('span', { class: 'meta' },
            `${fmtDur(e.canon.duration_s)} ${fmtSize(e.size)} ${e.composer ?? ''}`),
          el('span', { class: `badge b-${e.canon.status}` }, e.hidden ? 'hidden' : e.canon.status));
        row.onclick = (ev) => this.onTrackClick(ev, e, rowIndex);
        tree.append(row);
      }
    }

    const refused = this.doc.entries.filter((e) => e.canon.status === 'refused' || e.canon.status === 'unparsed');
    const one = this.selected.size === 1
      ? this.doc.entries.find((e) => e.id === [...this.selected][0]) : undefined;
    const panel = this.selected.size > 1 ? this.bulkPanel()
      : one ? this.details(one)
        : el('div', { class: 'detail empty' }, 'select a track · ↑↓ move · ←→ fold · space play · c canon · h hide · d delete');
    // re-rendering must not move the list under the mouse: restore the tree's scroll
    // position, and only chase the cursor when navigation came from the keyboard
    const prevScroll = (this.root.querySelector('.tree') as HTMLElement | null)?.scrollTop ?? 0;
    this.root.replaceChildren(this.toolbar(refused.length), el('div', { class: 'cols' }, tree, panel));
    tree.scrollTop = prevScroll;
    if (this.keyboardNav) {
      this.keyboardNav = false;
      this.root.querySelector('.row.cur, .folder.cur')?.scrollIntoView({ block: 'nearest' });
    }
  }

  private toolbar(refusedCount: number): HTMLElement {
    const search = el('input', { type: 'search', placeholder: 'filter…', value: this.filter });
    search.oninput = () => {
      this.filter = search.value;
      this.render();
    };
    const auto = el('input', { type: 'checkbox', id: 'autoplay' });
    auto.checked = this.autoplay;
    auto.onchange = () => {
      this.autoplay = auto.checked;
      try {
        localStorage.setItem(AUTOPLAY_KEY, this.autoplay ? '1' : '0');
      } catch { /* private mode */ }
      this.maybeAutoplay();
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
      el('label', { class: 'autoplay', for: 'autoplay', title: 'preview the selected track automatically' },
        auto, ' play on click'),
      el('span', { class: 'count' }, `${n} tracks${pvWarn}`),
      this.status);
  }

  // ---------------------------------------------------------------- panels

  private bulkPanel(): HTMLElement {
    const ids = [...this.selected];
    const d = el('div', { class: 'detail' });
    d.append(el('h2', {}, `${ids.length} tracks selected`));

    const dir = el('input', { placeholder: 'target folder ("" = top level)', value: this.selectedDir() });
    const move = el('button', {}, 'Move all here');
    move.onclick = () => void this.act(`move ${ids.length}`,
      () => post('/api/library/bulk', { op: 'move', ids, dir: dir.value.trim() }));
    d.append(el('div', { class: 'form' }, el('label', {}, 'folder', dir), move));

    const fields: [string, string][] = [['composer', 'composer'], ['sequencer', 'sequencer'],
      ['source_url', 'source url'], ['notes', 'notes']];
    const inputs = new Map<string, HTMLInputElement>();
    const form = el('div', { class: 'form' });
    for (const [key, label] of fields) {
      const inp = el('input', { placeholder: '(leave blank to keep)' });
      inputs.set(key, inp);
      form.append(el('label', {}, label, inp));
    }
    const apply = el('button', {}, 'Set on all');
    apply.onclick = () => {
      const body: Record<string, string> = {};
      for (const [k, inp] of inputs) if (inp.value.trim()) body[k] = inp.value.trim();
      if (!Object.keys(body).length) return this.note('nothing to set', true);
      void this.act(`edit ${ids.length}`,
        () => post('/api/library/bulk', { op: 'edit', ids, fields: body }));
    };
    form.append(apply);
    d.append(form);

    const hide = el('button', {}, 'Hide (h)');
    hide.onclick = () => this.hideSelected();
    const canon = el('button', {}, 'Re-canon (c)');
    canon.onclick = () => this.canonRun(ids);
    const rm = el('button', { class: 'danger' }, 'Delete (d)');
    rm.onclick = () => this.deleteSelected();
    d.append(el('div', { class: 'btnrow' }, hide, canon, rm));
    return d;
  }

  private details(e: Entry): HTMLElement {
    const d = el('div', { class: 'detail' });
    d.append(el('h2', {}, e.name), el('div', { class: 'path' }, e.path));

    this.setAudio(e.id);
    d.append(this.audio);

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

    const pathInp = el('input', { value: e.path });
    const move = el('button', {}, 'Move / rename file');
    move.onclick = () => this.act('move', () => post(`/api/library/${e.id}/move`, { path: pathInp.value.trim() }));
    d.append(el('div', { class: 'form' }, el('label', {}, 'file path', pathInp), move));

    const hide = el('button', {}, e.hidden ? 'Unhide (h)' : 'Hide from site (h)');
    hide.onclick = () => this.hideSelected();
    const rm = el('button', { class: 'danger' }, 'Delete (d)');
    rm.onclick = () => this.deleteSelected();
    d.append(el('div', { class: 'btnrow' }, hide, rm));

    const c = e.canon;
    const canonBox = el('div', { class: `canonbox b-${c.status}` },
      el('strong', {}, `canon: ${c.status}`),
      c.reason ? el('div', { class: 'reason' }, c.reason) : '');
    if (c.status === 'refused' && (c.reason ?? '').includes('program change')) {
      const fix = el('button', {}, 'Fix missing programs…');
      fix.onclick = () => this.injectFix(e);
      canonBox.append(fix);
    }
    const recheck = el('button', {}, 'Re-run canon (c)');
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
    const first = [...this.selected][0];
    const e = first ? this.doc?.entries.find((x) => x.id === first) : undefined;
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
