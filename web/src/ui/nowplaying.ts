/** Now playing panel: provenance, render command, gain/LUFS, legal note, song license, tier, status. */
import { engineLabel, SF2_INFO_KEYS, type CatalogDoc } from '../contracts/catalog';
import type { SetDoc } from '../contracts/set';
import { songTitle, type SongEntry } from '../contracts/songs';
import { clear, fmtBytes, h, setPressed } from './dom';

export interface NowPlayingActions {
  isFavorite(id: string): boolean;
  toggleFavorite(id: string): boolean;
}

/** the tier pill: what you are hearing right now — the scrubbing tier or the listening one */
export function tierLabel(t: 's' | 'l' | null, set: SetDoc): string {
  if (t === 'l') return `listening · ${set.listen.bitrate}k`;
  if (t === 's') return `scrubbing · ${set.scrub.bitrate}k`;
  return '';
}

/** the pill's tooltip: both tiers, named as the pill names them, with their bitrates */
export function tierTitle(set: SetDoc): string {
  return `audio tier: scrubbing (${set.scrub.bitrate} kbps) or listening (${set.listen.bitrate} kbps)`;
}

export class NowPlaying {
  readonly el: HTMLElement;
  private tierEl: HTMLElement;
  private statusEl: HTMLElement;
  private body: HTMLElement;
  private favoriteId: string | null = null;
  private favoriteBtn: HTMLButtonElement | null = null;

  constructor(private readonly catalog: CatalogDoc, private readonly set: SetDoc, private readonly song: SongEntry, private actions: NowPlayingActions) {
    this.tierEl = h('span', { class: 'tier', title: tierTitle(set) }, '');
    this.statusEl = h('span', { class: 'np-status' }, '');
    this.body = h('div', { class: 'np-body' }, h('p', { class: 'muted' }, 'Select a variant (↑/↓) to hear the song through it.'));
    this.el = h('section', { class: 'nowplaying', 'aria-live': 'polite' }, h('div', { class: 'np-head' }, h('span', { class: 'np-title' }, 'now playing'), this.tierEl, this.statusEl), this.body);
  }

  setTier(t: 's' | 'l' | null): void {
    this.tierEl.textContent = tierLabel(t, this.set);
    this.tierEl.className = `tier ${t ?? ''}`;
  }

  setStatus(text: string, kind = ''): void {
    this.statusEl.textContent = text;
    this.statusEl.className = `np-status ${kind}`;
  }

  private paintFav(btn: HTMLButtonElement, id: string): void {
    const on = this.actions.isFavorite(id);
    btn.textContent = on ? '♥ remove from favorites' : '♡ add to favorites';
    setPressed(btn, on);
  }

  private actionsBar(id: string): HTMLElement {
    const fav = h('button', { class: 'btn fav-btn', type: 'button', title: 'favorite / unfavorite (V)' }) as HTMLButtonElement;
    this.favoriteId = id;
    this.favoriteBtn = fav;
    this.paintFav(fav, id);
    fav.addEventListener('click', () => {
      this.actions.toggleFavorite(id);
      this.paintFav(fav, id);
      fav.blur();
    });
    const dl = h('button', { class: 'btn', type: 'button', disabled: true, title: 'downloads are coming in a later release' }, '⤓ download') as HTMLButtonElement;
    return h('div', { class: 'np-actions' }, fav, dl);
  }

  refreshFavorite(id: string): void {
    if (id === this.favoriteId && this.favoriteBtn) this.paintFav(this.favoriteBtn, id);
  }

  show(id: string | null): void {
    clear(this.body);
    this.favoriteId = null;
    this.favoriteBtn = null;
    if (!id) return;
    const v = this.catalog.byId.get(id);
    const sv = this.set.variants[id];
    const eng = this.catalog.engines.find((e) => e.id === v?.engine);
    const rows: [string, Node | string | null | undefined][] = [];
    rows.push(['id', h('code', null, id)]);
    rows.push(['engine', eng ? engineLabel(eng) : (v?.engine ?? '')]);
    if (v?.chip) rows.push(['chip', `${v.chip}${v.render?.core ? ' · core ' + v.render.core : ''}`]);
    if (v?.bank) {
      const b = v.bank;
      rows.push(['bank', `${b.kind === 'embedded' ? `#${b.number} ` : ''}${b.family ?? ''} ${b.name ?? ''}`.trim()]);
      if (b.tags.length) rows.push(['tags', h('span', null, b.tags.map((t) => h('span', { class: 'tag', title: tagHelp(t) }, t)))]);
    }
    const src = v?.source;
    if (src?.file) rows.push(['file', h('code', null, src.file)]);
    const sf2 = src?.sf2;
    if (sf2) {
      // SF2_INFO_KEYS is the whole set the catalog publishes: the free-prose ICMT comment is
      // deliberately not among them (it carries third-party PII — see issue #2)
      for (const k of SF2_INFO_KEYS) if (sf2[k]) rows.push([k, sf2[k]]);
    }
    if (src?.bytes) rows.push(['size', fmtBytes(src.bytes)]);
    if (sf2) rows.push(['presets', `${sf2.preset_count ?? '?'} presets · ${sf2.melodic_bank0 ?? '?'} melodic in bank 0 · ${sf2.has_drums ? 'drums' : 'no drums'}${sf2.ifil ? ` · sf ${sf2.ifil}` : ''}`]);
    if (v?.facets) {
      const f = v.facets;
      rows.push(['facets', ['completeness', 'bank_map', 'lineage', 'decade', 'size'].map((k) => (f[k] ? `${k}=${String(f[k])}` : '')).filter(Boolean).join('  ')]);
    }
    if (sv) rows.push(['loudness', `measured ${sv.lufs.toFixed(1)} LUFS, true peak ${sv.tp.toFixed(1)} dBTP → gain ${sv.gain_db >= 0 ? '+' : ''}${sv.gain_db.toFixed(1)} dB to reach ${this.set.lufs_target} LUFS`]);
    if (v?.render?.cmd) rows.push(['render', h('code', { class: 'cmd' }, v.render.cmd)]);
    if (v?.legal_note) rows.push(['note', h('em', null, v.legal_note)]);
    if (src?.license_flag) rows.push(['license flag', src.license_flag]);
    if (src?.collection) {
      const c = src.collection;
      rows.push(['from', h('span', null, 'Internet Archive · ', h('a', { href: c.url, target: '_blank', rel: 'noopener' }, c.title), c.torrent ? [' · ', h('a', { href: c.torrent, target: '_blank', rel: 'noopener' }, 'torrent')] : '')]);
    } else if (src?.url) rows.push(['source', h('a', { href: src.url, target: '_blank', rel: 'noopener' }, src.url)]);
    rows.push(['song', `${songTitle(this.song)} · ${this.song.license.id}`]);
    const dl = h('dl', { class: 'kv' });
    for (const [k, val] of rows) {
      if (val === null || val === undefined || val === '') continue;
      dl.append(h('dt', null, k), h('dd', null, val));
    }
    this.body.append(h('h2', { class: 'np-label' }, v?.label ?? id), this.actionsBar(id), dl);
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
