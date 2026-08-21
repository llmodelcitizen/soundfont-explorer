import { ContractError } from './songs';

export interface EngineInfo {
  id: string;
  label: string;
  version: string | null;
  commit: string | null;
  url: string | null;
  license: string | null;
}

export type Facets = Record<string, string | string[] | boolean | number | null | undefined>;

export interface Variant {
  id: string;
  slug: string;
  label: string;
  engine: string;
  chip: string;
  type: string;
  facets: Facets;
  bank: Record<string, unknown> | null;
  source: { file: string | null; sha256: string | null; bytes: number | null; url?: string | null; license_flag?: string | null; info?: Record<string, string> | null } | null;
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

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null;
const str = (x: unknown, d = ''): string => (typeof x === 'string' ? x : d);

export function parseCatalog(raw: unknown): CatalogDoc {
  if (!isObj(raw) || raw.schema !== 1) throw new ContractError('bad catalog document');
  const engines = (Array.isArray(raw.engines) ? raw.engines : []).filter(isObj).map((e) => ({
    id: str(e.id),
    label: str(e.label, str(e.id)),
    version: typeof e.version === 'string' ? e.version : null,
    commit: typeof e.commit === 'string' ? e.commit : null,
    url: typeof e.url === 'string' ? e.url : null,
    license: typeof e.license === 'string' ? e.license : null,
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
    return {
      id,
      slug: str(v.slug, id),
      label: str(v.label, id),
      engine: str(v.engine, String(f.engine ?? '')),
      chip: str(v.chip, String(f.chip ?? '')),
      type: str(v.type, String(f.type ?? '')),
      facets: f,
      bank: isObj(v.bank) ? v.bank : null,
      source: isObj(v.source) ? (v.source as Variant['source']) : null,
      render: isObj(v.render) ? (v.render as Variant['render']) : null,
      legal_note: typeof v.legal_note === 'string' ? v.legal_note : null,
      requires_rom: Boolean(v.requires_rom),
      aliases: Array.isArray(v.aliases) ? v.aliases.map(String) : [],
    };
  });
  const byId = new Map(variants.map((v) => [v.id, v]));
  return { schema: 1, engines, facets, variants, byId };
}
