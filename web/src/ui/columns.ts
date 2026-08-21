/** Column model for the variant list: definitions, cell values, sort values. */
import type { CatalogDoc, Variant } from '../contracts/catalog';
import type { SetDoc } from '../contracts/set';
import { fmtBytes } from './dom';

export type ColKey = 'idx' | 'chip' | 'label' | 'engine' | 'id' | 'bank' | 'size' | 'decade' | 'lineage' | 'coverage' | 'lufs' | 'gain' | 'fav' | 'dot';

export interface ColumnDef {
  key: ColKey;
  label: string;
  title: string;
  width: string;
  align?: 'right' | 'center';
  /** cannot be hidden */
  always?: boolean;
  /** shown in the compact (mobile) layout */
  compact?: boolean;
  defaultOn: boolean;
}

export const COLUMNS: ColumnDef[] = [
  { key: 'idx', label: '#', title: 'position in the current list', width: '3.2em', align: 'right', always: true, compact: true, defaultOn: true },
  { key: 'chip', label: 'chip', title: 'sound chip / format', width: '4.6em', compact: true, defaultOn: true },
  { key: 'label', label: 'name', title: 'name', width: 'minmax(0, 1fr)', always: true, compact: true, defaultOn: true },
  { key: 'engine', label: 'engine', title: 'render engine', width: '7.5em', defaultOn: false },
  { key: 'id', label: 'id', title: 'stable variant id', width: '9em', defaultOn: false },
  { key: 'bank', label: 'bank', title: 'FM bank family', width: '5.2em', defaultOn: true },
  { key: 'size', label: 'size', title: 'SoundFont size', width: '5.6em', align: 'right', defaultOn: true },
  { key: 'decade', label: 'decade', title: 'decade of origin', width: '4.4em', defaultOn: true },
  { key: 'lineage', label: 'lineage', title: 'lineage facet', width: '6.4em', defaultOn: false },
  { key: 'coverage', label: 'coverage', title: 'GM coverage', width: '6.4em', defaultOn: false },
  { key: 'lufs', label: 'LUFS', title: 'measured integrated loudness before gain', width: '4.8em', align: 'right', defaultOn: false },
  { key: 'gain', label: 'gain', title: 'gain applied to reach −16 LUFS', width: '4.8em', align: 'right', defaultOn: true },
  { key: 'fav', label: '♥', title: 'favourite', width: '1.3em', align: 'center', compact: true, defaultOn: true },
  { key: 'dot', label: '●', title: 'listened', width: '1em', align: 'center', compact: true, defaultOn: true },
];

export const DEFAULT_COLUMNS: ColKey[] = COLUMNS.filter((c) => c.defaultOn).map((c) => c.key);
export const COMPACT_COLUMNS: ColKey[] = COLUMNS.filter((c) => c.compact).map((c) => c.key);

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
