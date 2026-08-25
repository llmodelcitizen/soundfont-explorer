/** Column model for the variant list: definitions, cell values, sort values. */
import type { CatalogDoc, Variant } from '../contracts/catalog';
import type { SetDoc } from '../contracts/set';
import { fmtBytes } from './dom';

export type ColKey = 'idx' | 'chip' | 'label' | 'engine' | 'id' | 'bank' | 'size' | 'decade' | 'lineage' | 'coverage' | 'lufs' | 'gain' | 'fav' | 'dot';

/** Which columns are on by default lives in state/prefs.ts (DEFAULT_PREFS.columns / mobileColumns). */
export interface ColumnDef {
  key: ColKey;
  label: string;
  /** header tooltip; a function may read the set document (e.g. the loudness target) */
  title: string | ((set?: SetDoc) => string);
  width: string;
  /** extra classes on the body cell, after `cell col-<key>` and the alignment */
  cls: string;
  align?: 'right' | 'center';
  /** cannot be hidden */
  always?: boolean;
  /** fixed cell text (the row's classes say whether it is lit) instead of cellText() */
  glyph?: string;
  /** too narrow for a sort arrow: indicate the sort by colour only */
  noArrow?: boolean;
}

export const COLUMNS: ColumnDef[] = [
  { key: 'idx', label: '#', title: 'position in the current list', width: '3.2em', cls: 'idx', align: 'center', always: true },
  { key: 'chip', label: 'chip', title: 'sound chip / format', width: '4.6em', cls: 'badge' },
  { key: 'label', label: 'name', title: 'name', width: 'minmax(14em, 1fr)', cls: 'label', always: true },
  { key: 'engine', label: 'engine', title: 'render engine', width: '7.5em', cls: 'meta' },
  { key: 'id', label: 'id', title: 'stable variant id', width: '9em', cls: 'meta' },
  { key: 'bank', label: 'bank', title: 'FM bank family', width: '5.2em', cls: 'meta' },
  { key: 'size', label: 'size', title: 'SoundFont size', width: '5.6em', cls: 'meta', align: 'right' },
  { key: 'decade', label: 'decade', title: 'decade of origin', width: '4.4em', cls: 'meta' },
  { key: 'lineage', label: 'lineage', title: 'lineage facet', width: '6.4em', cls: 'meta' },
  { key: 'coverage', label: 'coverage', title: 'GM coverage', width: '6.4em', cls: 'meta' },
  { key: 'lufs', label: 'LUFS', title: 'measured integrated loudness before gain', width: '4.8em', cls: 'num meta', align: 'right' },
  { key: 'gain', label: 'gain', width: '4.8em', cls: 'num meta', align: 'right',
    title: (set) => set ? `gain applied to reach ${String(set.lufs_target).replace('-', '−')} LUFS` : 'gain applied to reach the target loudness' },
  { key: 'fav', label: '♥', title: 'favorite', width: '1.4em', cls: 'fav', align: 'center', glyph: '♥', noArrow: true },
  { key: 'dot', label: '●', title: 'listened', width: '1.4em', cls: 'dot', align: 'center', glyph: '', noArrow: true },
];

export const columnDef = (key: ColKey): ColumnDef => COLUMNS.find((c) => c.key === key)!;
export const columnTitle = (c: ColumnDef, set?: SetDoc): string => (typeof c.title === 'function' ? c.title(set) : c.title);

/** the columns shown for a preference list: always-on ones are forced; model order */
export function visibleColumns(keys: readonly string[]): ColumnDef[] {
  const want = new Set(keys);
  return COLUMNS.filter((c) => c.always || want.has(c.key));
}

/**
 * Minimum row width for a column set, as a CSS length: the em part is resolved by the browser
 * where `--row-min` is used (the rows, which share the list's font size), so nothing has to be
 * measured on a detached element and the value follows a theme's font size on its own.
 */
export function rowMinWidth(cols: readonly ColumnDef[]): string {
  const em = cols.reduce((n, c) => {
    const min = /^minmax\(([\d.]+)em/.exec(c.width)?.[1]; // 'minmax(14em, 1fr)' counts its minimum
    return n + parseFloat(min ?? c.width);
  }, 0);
  const px = 8 * (cols.length - 1) + 26; // column gaps + row padding
  return `calc(${Math.round(em * 100) / 100}em + ${px}px)`;
}

const CHIP_SUFFIX = /\s*[[(](OPL2|OPL3|ESFM|CQM|OPN2|OPNA|OPLL|SCC|SF2)[\])]\s*$/i;

/** name without a chip tag the chip column already shows */
export function displayLabel(v: Variant | undefined, id: string): string {
  return (v?.label ?? id).replace(CHIP_SUFFIX, '');
}

export function chipLabel(v: Variant | undefined): string {
  if (!v) return '?';
  const chip = (v.chip || '').toLowerCase();
  switch (chip) {
    case 'sf2':
      return 'SF2';
    case 'pcm_rom':
      return 'SC-55';
    case 'la':
      return 'MT-32';
    case 'gus':
      return 'GUS';
    default:
      return chip ? chip.toUpperCase() : v.engine.toUpperCase();
  }
}

export const ENGINE_SHORT: Record<string, string> = {
  fluidsynth: 'FluidSynth',
  adlmidi: 'libADLMIDI',
  opnmidi: 'libOPNMIDI',
  edmidi: 'libEDMIDI',
  timidity: 'TiMidity++',
  sc55: 'Nuked-SC55',
  munt: 'Munt',
};

export interface CellContext {
  catalog: CatalogDoc;
  set: SetDoc;
  isFavorite(id: string): boolean;
  listenedSeconds(id: string): number;
  listened(id: string): boolean;
}

const decadeYear = (d: unknown): number => {
  const m = /^(\d{4})/.exec(String(d ?? ''));
  return m ? Number(m[1]) : Number.POSITIVE_INFINITY; // unknown sorts last
};

/** text shown in the cell (fav/dot are rendered as glyphs by the list itself) */
export function cellText(key: ColKey, id: string, idx: number, ctx: CellContext): string {
  const v = ctx.catalog.byId.get(id);
  const sv = ctx.set.variants[id];
  const f = v?.facets ?? {};
  switch (key) {
    case 'idx':
      return String(idx + 1);
    case 'chip':
      return chipLabel(v);
    case 'label':
      return displayLabel(v, id);
    case 'engine':
      return ENGINE_SHORT[v?.engine ?? ''] ?? v?.engine ?? '';
    case 'id':
      return id;
    case 'bank':
      return v?.bank && typeof v.bank['family'] === 'string' ? String(v.bank['family']) : '';
    case 'size':
      return v?.source?.bytes ? fmtBytes(v.source.bytes) : '';
    case 'decade':
      return f.decade && f.decade !== 'unknown' ? String(f.decade) : '';
    case 'lineage':
      return f.lineage && f.lineage !== 'generic' ? String(f.lineage).replace(/_/g, ' ') : '';
    case 'coverage':
      return f.completeness ? String(f.completeness).replace(/_/g, ' ') : '';
    case 'lufs':
      return sv ? sv.lufs.toFixed(1) : '';
    case 'gain':
      return sv ? `${sv.gain_db >= 0 ? '+' : ''}${sv.gain_db.toFixed(1)} dB` : '';
    case 'fav':
      return ctx.isFavorite(id) ? '♥' : '';
    case 'dot':
      return ctx.listened(id) ? '●' : '';
    default:
      return '';
  }
}

/** comparable value for sorting; null = blank (always sorted last, whatever the direction) */
export function sortValue(key: ColKey, id: string, canonicalIdx: number, ctx: CellContext): number | string | null {
  const v = ctx.catalog.byId.get(id);
  const sv = ctx.set.variants[id];
  const f = v?.facets ?? {};
  switch (key) {
    case 'idx':
      return canonicalIdx;
    case 'size':
      return v?.source?.bytes ?? null;
    case 'decade': {
      const y = decadeYear(f.decade);
      return Number.isFinite(y) ? y : null;
    }
    case 'lufs':
      return sv?.lufs ?? null;
    case 'gain':
      return sv?.gain_db ?? null;
    case 'fav':
      return ctx.isFavorite(id) ? 0 : 1;
    case 'dot':
      return -ctx.listenedSeconds(id);
    default:
      return cellText(key, id, canonicalIdx, ctx).toLowerCase() || null;
  }
}

export function sortIds(ids: string[], key: ColKey, dir: 1 | -1, canonical: Map<string, number>, ctx: CellContext): string[] {
  const keyed = ids.map((id) => ({ id, k: sortValue(key, id, canonical.get(id) ?? 0, ctx), c: canonical.get(id) ?? 0 }));
  keyed.sort((a, b) => {
    if (a.k === null && b.k === null) return a.c - b.c;
    if (a.k === null) return 1;
    if (b.k === null) return -1;
    if (a.k === b.k) return a.c - b.c;
    if (typeof a.k === 'number' && typeof b.k === 'number') return (a.k - b.k) * dir;
    return String(a.k).localeCompare(String(b.k)) * dir;
  });
  return keyed.map((x) => x.id);
}
