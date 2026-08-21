/** Now playing panel: provenance, render command, gain/LUFS, legal note, song license, tier, status. */
import type { CatalogDoc, Variant } from '../contracts/catalog';
import type { SetDoc } from '../contracts/set';
import type { SongEntry } from '../contracts/songs';
import { clear, fmtBytes, h } from './dom';

export class NowPlaying {
  readonly el: HTMLElement;
  private tierEl: HTMLElement;
  private statusEl: HTMLElement;
  private body: HTMLElement;

  constructor(private catalog: CatalogDoc, private set: SetDoc, private song: SongEntry) {
    this.tierEl = h('span', { class: 'tier', title: 'audio tier: scrub (48 kbps) or listen (96 kbps)' }, '');
    this.statusEl = h('span', { class: 'np-status' }, '');
    this.body = h('div', { class: 'np-body' }, h('p', { class: 'muted' }, 'Select a variant (↑/↓) to hear the song through it.'));
    this.el = h('section', { class: 'nowplaying', 'aria-live': 'polite' }, h('div', { class: 'np-head' }, h('span', { class: 'np-title' }, 'now playing'), this.tierEl, this.statusEl), this.body);
  }

  setContext(catalog: CatalogDoc, set: SetDoc, song: SongEntry): void {
    this.catalog = catalog;
    this.set = set;
    this.song = song;
  }

  setTier(t: 's' | 'l' | null): void {
    this.tierEl.textContent = t === 'l' ? 'listen · 96k' : t === 's' ? 'scrub · 48k' : '';
    this.tierEl.className = `tier ${t ?? ''}`;
  }

  setStatus(text: string, kind = ''): void {
    this.statusEl.textContent = text;
    this.statusEl.className = `np-status ${kind}`;
  }

  show(id: string | null): void {
    clear(this.body);
    if (!id) return;
    const v = this.catalog.byId.get(id);
    const sv = this.set.variants[id];
    const eng = this.catalog.engines.find((e) => e.id === v?.engine);
    const rows: [string, Node | string | null | undefined][] = [];
    rows.push(['id', h('code', null, id)]);
    rows.push(['engine', eng ? `${eng.label}${eng.version ? ' ' + eng.version : ''}${eng.commit ? ' (' + eng.commit + ')' : ''}` : (v?.engine ?? '')]);
    if (v?.chip) rows.push(['chip', `${v.chip}${v.render?.core ? ' · core ' + v.render.core : ''}`]);
    if (v?.bank) {
      const b = v.bank;
      const tags = Array.isArray(b['tags']) ? (b['tags'] as string[]) : [];
      rows.push(['bank', `${b['kind'] === 'embedded' ? '#' + String(b['number']) + ' ' : ''}${String(b['family'] ?? '')} ${String(b['name'] ?? '')}`.trim()]);
      if (tags.length) rows.push(['tags', h('span', null, tags.map((t) => h('span', { class: 'tag', title: tagHelp(t) }, t)))]);
    }
    const src = v?.source;
    if (src?.file) rows.push(['file', h('code', null, src.file)]);
    const info = (src?.info ?? null) as Record<string, string> | null;
    if (info) {
      for (const k of ['INAM', 'IENG', 'ICRD', 'IPRD', 'ICOP', 'ISFT']) if (info[k]) rows.push([k, info[k]]);
      if (info['ICMT']) rows.push(['ICMT', h('span', { class: 'icmt' }, info['ICMT'].slice(0, 600))]);
    }
    if (src?.bytes) rows.push(['size', fmtBytes(src.bytes)]);
    const sf = (src as Record<string, unknown> | null)?.['sf2'] as Record<string, unknown> | undefined;
    if (sf) rows.push(['presets', `${String(sf['preset_count'] ?? '?')} presets · ${String(sf['melodic_bank0'] ?? '?')} melodic in bank 0 · ${sf['has_drums'] ? 'drums' : 'no drums'}`]);
    if (v?.facets) {
      const f = v.facets;
      rows.push(['facets', ['completeness', 'bank_map', 'lineage', 'decade', 'size'].map((k) => (f[k] ? `${k}=${String(f[k])}` : '')).filter(Boolean).join('  ')]);
    }
    if (sv) rows.push(['loudness', `measured ${sv.lufs.toFixed(1)} LUFS, true peak ${sv.tp.toFixed(1)} dBTP → gain ${sv.gain_db >= 0 ? '+' : ''}${sv.gain_db.toFixed(1)} dB to reach ${this.set.lufs_target} LUFS`]);
    if (v?.render?.cmd) rows.push(['render', h('code', { class: 'cmd' }, v.render.cmd)]);
    if (v?.legal_note) rows.push(['note', h('em', null, v.legal_note)]);
    if (src?.license_flag) rows.push(['license flag', src.license_flag]);
    if (src?.url) rows.push(['source', h('a', { href: src.url, target: '_blank', rel: 'noopener' }, src.url)]);
    rows.push(['song', `${this.song.title}${this.song.composer ? ' — ' + this.song.composer : ''} · ${this.song.license.id}`]);
    const dl = h('dl', { class: 'kv' });
    for (const [k, val] of rows) {
      if (val === null || val === undefined || val === '') continue;
      dl.append(h('dt', null, k), h('dd', null, val));
    }
    this.body.append(h('h2', { class: 'np-label' }, v?.label ?? id), dl);
  }
}

function tagHelp(t: string): string {
  switch (t.toLowerCase()) {
    case 'non_gm':
    case 'non-gm':
      return 'bank is not General MIDI: instruments are mapped for a specific game';
    case 'mt32':
    case 'mt-32':
      return 'MT-32 instrument layout, not GM';
    case 'miss_ins':
    case 'miss-ins':
      return 'some instruments are missing';
    case 'fourop':
      return 'uses 4-operator voices';
    case 'melodic_only':
      return 'no percussion';
    default:
      return t;
  }
}
