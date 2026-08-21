import { ContractError, isNum, isObj, isStr } from './songs';

export interface EngineInfo {
  id: string;
  label: string;
  version: string | null;
  commit: string | null;
  url: string | null;
  license: string | null;
}

export type Facets = Record<string, string | string[] | boolean | number | null | undefined>;

/** SF2 INFO chunk strings shown in the now-playing panel, in display order */
export const SF2_INFO_KEYS = ['INAM', 'IENG', 'ICRD', 'IPRD', 'ICOP', 'ISFT'] as const;

/** SoundFont INFO chunk plus the stats the catalog builder adds; null/absent for non-SF2 sources */
export interface Sf2Info extends Partial<Record<(typeof SF2_INFO_KEYS)[number] | 'ICMT', string | null>> {
  ifil?: string | null;
  preset_count?: number | null;
  melodic_bank0?: number | null;
  has_drums?: boolean | null;
  bank_count?: number | null;
}

export interface Source {
  file: string | null;
  sha256: string | null;
  bytes: number | null;
  url: string | null;
  license_flag: string | null;
  sf2: Sf2Info | null;
  collection: { id: string; title: string; url: string; torrent: string | null } | null;
}

/** FM bank (embedded in the engine or a bank file); tags are the quality flags that are set */
export interface Bank {
  kind: string | null;
  number: number | null;
  family: string | null;
  name: string | null;
  tags: string[];
}

export interface Variant {
  id: string;
  slug: string;
  label: string;
  engine: string;
  chip: string;
  type: string;
  facets: Facets;
  bank: Bank | null;
  source: Source | null;
  render: { cmd?: string; core?: string } | null;
  legal_note: string | null;
  requires_rom: boolean;
  aliases: string[];
}

export interface CatalogDoc {
  schema: 1;
  engines: EngineInfo[];
  facets: Record<string, { value: string; count: number }[]>;
  variants: Variant[];
  byId: Map<string, Variant>;
}

const str = (x: unknown, d = ''): string => (isStr(x) ? x : d);
const strOrNull = (x: unknown): string | null => (isStr(x) ? x : null);

/** tag names from the catalog's two spellings: a list of names, or {name: boolean} */
export function tagNames(raw: unknown): string[] {
  if (Array.isArray(raw)) return raw.map(String);
  if (isObj(raw)) return Object.keys(raw).filter((k) => raw[k]);
  return [];
}

/** "version (commit)" — whichever parts the engine reports */
export function engineVersion(e: EngineInfo): string {
  return [e.version, e.commit && `(${e.commit})`].filter(Boolean).join(' ');
}

/** "label version (commit)" */
export function engineLabel(e: EngineInfo): string {
  return [e.label, engineVersion(e)].filter(Boolean).join(' ');
}

function parseSource(s: Record<string, unknown>): Source {
  const c = isObj(s.collection) ? s.collection : null;
  return {
    file: strOrNull(s.file),
    sha256: strOrNull(s.sha256),
    bytes: isNum(s.bytes) ? s.bytes : null,
    url: strOrNull(s.url),
    license_flag: strOrNull(s.license_flag),
    sf2: isObj(s.sf2) ? (s.sf2 as Sf2Info) : null,
    collection: c && { id: str(c.id), title: str(c.title), url: str(c.url), torrent: strOrNull(c.torrent) },
  };
}

export function parseCatalog(raw: unknown): CatalogDoc {
  if (!isObj(raw) || raw.schema !== 1) throw new ContractError('bad catalog document');
  const engines = (Array.isArray(raw.engines) ? raw.engines : []).filter(isObj).map((e) => ({
    id: str(e.id),
    label: str(e.label, str(e.id)),
    version: strOrNull(e.version),
    commit: strOrNull(e.commit),
    url: strOrNull(e.url),
    license: strOrNull(e.license),
  }));
  const facets: CatalogDoc['facets'] = {};
  if (isObj(raw.facets)) {
    for (const [k, v] of Object.entries(raw.facets)) {
      if (Array.isArray(v)) facets[k] = v.filter(isObj).map((x) => ({ value: String(x.value), count: Number(x.count) || 0 }));
    }
  }
  if (!Array.isArray(raw.variants)) throw new ContractError('catalog.variants missing');
  const variants: Variant[] = raw.variants.filter(isObj).map((v) => {
    const id = str(v.id);
    if (!id) throw new ContractError('variant without id');
    const f = isObj(v.facets) ? (v.facets as Facets) : {};
    const b = isObj(v.bank) ? v.bank : null;
    return {
      id,
      slug: str(v.slug, id),
      label: str(v.label, id),
      engine: str(v.engine, String(f.engine ?? '')),
      chip: str(v.chip, String(f.chip ?? '')),
      type: str(v.type, String(f.type ?? '')),
      facets: f,
      bank: b && { kind: strOrNull(b.kind), number: isNum(b.number) ? b.number : null, family: strOrNull(b.family), name: strOrNull(b.name), tags: tagNames(b.tags) },
      source: isObj(v.source) ? parseSource(v.source) : null,
      render: isObj(v.render) ? (v.render as Variant['render']) : null,
      legal_note: strOrNull(v.legal_note),
      requires_rom: Boolean(v.requires_rom),
      aliases: Array.isArray(v.aliases) ? v.aliases.map((a) => (isObj(a) ? [a.file, a.label].filter(isStr).join(' ') : String(a))) : [],
    };
  });
  const byId = new Map(variants.map((v) => [v.id, v]));
  return { schema: 1, engines, facets, variants, byId };
}
